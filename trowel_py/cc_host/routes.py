"""提供 Claude Code 会话的 HTTP 与 SSE 兼容路由。

本模块持有进程内 session registry、多开索引和当前选择；会话历史与 checkpoint
仍保存在磁盘。生命周期算法位于 session_lifecycle，公开 facade 保留原有依赖边界。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, AsyncIterator

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse

from trowel_py.agent_capacity import (
    DELEGATE_CONNECTION_LIMIT,
    USER_CONNECTION_LIMIT,
    USER_RUNNING_LIMIT,
)
from trowel_py.cc_host import checkpoint
from trowel_py.cc_host import session_lifecycle
from trowel_py.cc_host.history import parse_history
from trowel_py.cc_host.models import list_models
from trowel_py.cc_host.service import CCHost
from trowel_py.cc_host.session_scan import list_sessions
from trowel_py.cc_host.slash_items import list_slash_items
from trowel_py.resource_lifecycle.processes import ProcessController
from trowel_py.resource_lifecycle.registry import ResourceRegistry
from trowel_py.cc_host.schemas import (
    AnswerElicitRequest,
    CreateSessionRequest,
    ErrorEvent,
    RevertRequest,
    SendMessageRequest,
)

router = APIRouter()

# 历史列表只返回最近 10 条，响应中的 meta.total 仍报告磁盘总数。
_HISTORY_DROPDOWN_LIMIT = 10

# registry 与派生索引由本模块持有；进程重启只清运行中状态，不影响磁盘历史。
_REGISTRY: dict[str, CCHost] = {}

_WORKDIR_INDEX: dict[
    str, set[str]
] = {}  # workdir → {sid}（命名序号 + 按 workdir 查询）
_SESSION_NAMES: dict[str, str] = {}  # sid → 显示名（basename + #N）
_ACTIVE_SID: str | None = None  # 当前活跃 session（多开切换）
# MAX_RUNNING 仅保留公开兼容；当前路由只执行连接数门禁。
MAX_RUNNING = USER_RUNNING_LIMIT
MAX_CONNECTIONS = USER_CONNECTION_LIMIT
MAX_DELEGATE_CONNECTIONS = DELEGATE_CONNECTION_LIMIT


def get_registry() -> dict[str, CCHost]:
    """返回 FastAPI Depends 与 Agent Hub 共享的进程内 registry。"""

    return _REGISTRY


def get_active_session_id() -> str | None:
    """返回当前选中的 CC 会话 ID。"""

    return _ACTIVE_SID


def set_active_session_id(session_id: str | None) -> None:
    """更新当前会话选择，不校验 session ID 是否已注册。

    Agent Hub 通过此入口同步 CC 选择。
    """

    global _ACTIVE_SID
    _ACTIVE_SID = session_id


def _require(sid: str, registry: dict[str, CCHost]) -> CCHost:
    """只返回公开用户 host；内部 owner 会话与未知 id 都表现为 404。"""

    host = registry.get(sid)
    if host is None or getattr(host, "session_kind", "user") == "discussion":
        raise HTTPException(status_code=404, detail=f"session {sid} not found")
    return host


def _non_user_cc_session_ids(request: Request) -> frozenset[str]:
    """读取 Agent Host 长期登记的非用户 CC 会话身份。

    Args:
        request: 用于取得应用唯一 Session Hub 的公开 HTTP 请求。

    Returns:
        必须从旧 CC 恢复入口和历史列表排除的原生会话 ID；独立路由测试未装配
        Session Hub 时返回空集合。
    """

    hub = getattr(request.app.state, "agent_hub", None)
    if hub is None:
        return frozenset()
    from trowel_py.agent_host.binding import Runtime

    return hub.non_user_native_ids(Runtime.CLAUDE_CODE)


def _sse(event: object) -> str:
    """将 Pydantic 事件编码为一条 SSE data 消息。"""

    return f"data: {event.model_dump_json()}\n\n"  # type: ignore[attr-defined]


@dataclass(frozen=True)
class OpenedCcSession:
    """已经注册的 CC 会话。

    Attributes:
        sid: Trowel 生成的 session ID。
        host: 会话对应的 CC host。
        name: 用于界面展示的会话名。
    """

    sid: str
    host: CCHost
    name: str


def open_cc_session(
    req: CreateSessionRequest,
    request: Request,
    registry: dict[str, CCHost] | None = None,
) -> OpenedCcSession:
    """使用应用中的启动配置创建并注册 CC 会话。

    通过工作目录和容量校验后，本函数会创建该会话专用的 MCP 配置；请求中的
    功能开关决定配置内容。
    配置由 host 持有并在关闭时删除。host 构造失败时立即删除配置，且不登记会话。

    Args:
        req: 工作目录、恢复目标和会话功能开关。
        request: 提供代理地址和 CC settings 路径的当前 HTTP 请求。
        registry: 接收新会话的 registry；为 `None` 时使用模块共享 registry。

    Returns:
        已注册会话的 ID、host 和显示名称。

    Raises:
        HTTPException: 工作目录不存在或当前会话数已达上限。
    """

    try:
        return open_cc_session_configured(
            req,
            registry,
            proxy_base_url=getattr(request.app.state, "proxy_base_url", None),
            settings_path=getattr(request.app.state, "cc_settings_path", None),
        )
    except session_lifecycle.CcWorkdirNotFoundError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except session_lifecycle.CcCapacityError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


def open_cc_session_configured(
    req: CreateSessionRequest,
    registry: dict[str, CCHost] | None = None,
    *,
    proxy_base_url: str | None = None,
    settings_path: str | Path | None = None,
    display_name: str | None = None,
    process_controller: ProcessController | None = None,
    resource_registry: ResourceRegistry | None = None,
    owned_settings_path: bool = False,
    close_callback: Any | None = None,
    memory_mcp_enabled: bool | None = None,
) -> OpenedCcSession:
    """使用显式代理和 settings 配置创建并注册 CC 会话。

    创建成功后登记会话及其显示名称和工作目录，并把新会话设为当前选择。

    Args:
        req: 工作目录、恢复目标和会话功能开关。
        registry: 接收新会话的 registry；为 `None` 时使用模块共享 registry。
        proxy_base_url: CC 子进程使用的代理地址；为 `None` 时不配置代理。
        settings_path: 用于构造 CC 启动环境的 settings 文件；为 `None` 时不读取。
        display_name: Agent Hub 已按双 runtime 可见集合分配的临时名称；为 `None`
            时只根据旧版 CC 路由当前已登记的用户会话分配。
        process_controller: 核验并终止 CC 独立进程组的实现。
        resource_registry: 登记 CC 会话临时资源的当前应用账本。
        owned_settings_path: 是否由会话 host 删除传入的私有 settings。
        close_callback: 会话清理后执行的一次性代理租约释放函数。
        memory_mcp_enabled: 是否挂载 Memory MCP；None 时沿用正文注入开关。

    Returns:
        已注册会话的 ID、host 和显示名称。
    """

    target_registry = _REGISTRY if registry is None else registry
    owned_resource_config: dict[str, Any] = {}
    if owned_settings_path or close_callback is not None:
        owned_resource_config = {
            "owned_settings_path": owned_settings_path,
            "close_callback": close_callback,
        }
    if memory_mcp_enabled is not None:
        owned_resource_config["memory_mcp_enabled"] = memory_mcp_enabled
    sid, host, name = session_lifecycle.open_session(
        req,
        target_registry,
        proxy_base_url=proxy_base_url,
        settings_path=settings_path,
        workdir_index=_WORKDIR_INDEX,
        session_names=_SESSION_NAMES,
        max_connections=MAX_CONNECTIONS,
        max_delegate_connections=MAX_DELEGATE_CONNECTIONS,
        host_factory=CCHost,
        display_name=display_name,
        process_controller=process_controller,
        resource_registry=resource_registry,
        **owned_resource_config,
    )
    if req.session_kind == "user":
        set_active_session_id(sid)
    return OpenedCcSession(sid=sid, host=host, name=name)


@router.post("/sessions")
def create_session(
    req: CreateSessionRequest,
    request: Request,
    registry: dict[str, CCHost] = Depends(get_registry),
) -> dict:
    """创建新的 CC 会话，可通过原生会话 id 恢复已有会话。"""
    if req.session_kind == "discussion":
        raise HTTPException(status_code=404, detail="session kind not found")
    if req.resume_from is not None and req.resume_from in _non_user_cc_session_ids(
        request
    ):
        raise HTTPException(status_code=404, detail="session not found")
    opened = open_cc_session(req, request, registry)
    return {
        "success": True,
        "data": {
            "session_id": opened.sid,
            "cc_session_id": req.resume_from,
            "model": opened.host.model,
            "name": opened.name,
            "revert_enabled": checkpoint.is_enabled()
            and checkpoint.is_git_repo(req.workdir),
            "memory_enabled": req.memory_enabled,
            "profile_enabled": req.profile_enabled,
        },
        "error": None,
    }


@router.get("/sessions/active")
def list_active_sessions(
    registry: dict[str, CCHost] = Depends(get_registry),
) -> dict:
    """列出当前进程注册的 CC 会话及 active id。

    `connected` 表示 CC 子进程是否仍存活。
    """
    sessions = session_lifecycle.list_live_sessions(registry, _SESSION_NAMES)
    return {
        "success": True,
        "data": {"sessions": sessions, "active_id": get_active_session_id()},
        "error": None,
    }


@router.post("/sessions/{sid}/activate")
def activate_session(
    sid: str,
    registry: dict[str, CCHost] = Depends(get_registry),
) -> dict:
    """切换当前选中的 CC 会话，不关闭其他会话。"""
    _require(sid, registry)
    set_active_session_id(sid)
    return {"success": True, "data": {"active_id": sid}, "error": None}


@router.post("/sessions/{sid}/messages")
async def send_message(
    sid: str,
    body: SendMessageRequest,
    registry: dict[str, CCHost] = Depends(get_registry),
) -> StreamingResponse:
    """发送消息，并以 SSE 流返回 Trowel 事件。
    流内异常以终态 host_error 事件返回。"""
    host = _require(sid, registry)

    async def gen() -> AsyncIterator[bytes]:
        """逐帧输出 host 事件；流内异常转换为终态 ErrorEvent。"""

        try:
            async for event in host.send(body.text):
                yield _sse(event).encode()
        except Exception as exc:  # noqa: BLE001 — 流已建立，只能用事件传递异常。
            yield _sse(
                ErrorEvent(type="error", subclass="host_error", errors=[str(exc)])
            ).encode()

    return StreamingResponse(gen(), media_type="text/event-stream")


@router.post("/sessions/{sid}/interrupt")
async def interrupt(
    sid: str,
    registry: dict[str, CCHost] = Depends(get_registry),
) -> dict:
    """中断当前 turn；会话仍保留供后续发送。"""
    host = _require(sid, registry)
    await host.interrupt()
    return {"success": True, "data": {"interrupted": True}, "error": None}


@router.post("/sessions/{sid}/answer")
async def answer_elicit(
    sid: str,
    body: AnswerElicitRequest,
    registry: dict[str, CCHost] = Depends(get_registry),
) -> dict:
    """回答或取消待处理的 AskUserQuestion；操作成功后 CC 继续执行。"""
    host = _require(sid, registry)
    if body.cancel:
        ok = await host.cancel_elicit()
    else:
        ok = await host.answer_elicit(body.answers)
    return {
        "success": ok,
        "data": {"answered": ok},
        "error": None if ok else "no_pending_elicit",
    }


@router.post("/sessions/{sid}/revert")
async def revert_turn(
    sid: str,
    body: RevertRequest,
    registry: dict[str, CCHost] = Depends(get_registry),
) -> dict:
    """恢复到指定轮次开始前的工作树和 CC 历史。

    恢复后结束当前 CC 子进程，下一次发送将从恢复后的历史启动。找不到恢复点时
    返回 404；工作目录不是 Git 仓库时返回 400。
    """
    host = _require(sid, registry)
    try:
        meta = checkpoint.revert(host.workdir, body.turn_id)
    except checkpoint.NotAGitRepoError:
        raise HTTPException(status_code=400, detail="workdir is not a git repo")
    except checkpoint.UnknownCheckpointError:
        raise HTTPException(
            status_code=404, detail=f"checkpoint {body.turn_id} not found"
        )
    # reload 丢弃内存进程；下一次发送从截断后的 jsonl 恢复。
    await host.reload()
    return {
        "success": True,
        "data": {
            "reverted_turn_id": body.turn_id,
            "cc_session_id": meta.cc_session_id,
            "jsonl_offset": meta.jsonl_offset,
        },
        "error": None,
    }


@router.get("/sessions")
def list_history(
    request: Request,
    workdir: str = Query(..., min_length=1),
) -> dict:
    """列出工作目录最近 10 个可恢复 CC 会话，并在 meta.total 返回磁盘总数。"""
    visible = list_sessions(
        workdir,
        excluded_ids=_non_user_cc_session_ids(request),
    )
    items = [asdict(s) for s in visible[:_HISTORY_DROPDOWN_LIMIT]]
    return {
        "success": True,
        "data": items,
        "error": None,
        "meta": {"total": len(visible), "limit": _HISTORY_DROPDOWN_LIMIT},
    }


@router.get("/sessions/{sid}/history")
def get_history(
    sid: str,
    registry: dict[str, CCHost] = Depends(get_registry),
) -> dict:
    """将已保存的 CC 历史回放为 Trowel 事件。
    原生会话 id 缺失或历史文件不存在时返回空列表。"""
    host = _require(sid, registry)
    cc_session_id = host.cc_session_id
    if not cc_session_id:
        return {"success": True, "data": [], "error": None}
    events = parse_history(host.workdir, cc_session_id)
    return {"success": True, "data": [e.model_dump() for e in events], "error": None}


@router.get("/models")
def list_models_endpoint() -> dict:
    """返回 CC settings 中可用的模型别名及其实际模型。"""
    items = [asdict(m) for m in list_models()]
    return {"success": True, "data": items, "error": None}


def _init_roster_for_workdir(workdir: str, registry: dict[str, CCHost]) -> list[str]:
    """返回指定工作目录中可用的初始化命令。

    优先读取该目录当前选中会话的命令；该会话不属于目标目录或没有命令时，
    再检查同目录的其他会话。

    Args:
        workdir: 要查询的工作目录。
        registry: 提供初始化命令的当前 CC 会话。

    Returns:
        第一个可用的初始化命令列表；没有可用命令时返回空列表。
    """
    return session_lifecycle.init_roster_for_workdir(
        workdir,
        registry,
        _WORKDIR_INDEX,
        get_active_session_id(),
    )


@router.get("/slash-items")
def list_slash_items_endpoint(
    workdir: str = Query(..., min_length=1),
    registry: dict[str, CCHost] = Depends(get_registry),
) -> dict:
    """返回工作目录可用的 slash command 与 skill，并合并同目录会话的初始化名单。"""
    init_roster = _init_roster_for_workdir(workdir, registry)
    items = [asdict(i) for i in list_slash_items(workdir, init_roster=init_roster)]
    return {"success": True, "data": items, "error": None}


@router.get("/list-dir")
def list_dir(
    path: str = Query(..., min_length=1),
) -> dict:
    """列出指定目录的直接非隐藏子目录；路径无效时返回 400。"""
    p = Path(path).expanduser()
    if not p.is_dir():
        raise HTTPException(status_code=400, detail="not a directory")
    children = [
        {"name": sub.name, "path": str(sub)}
        for sub in sorted(p.iterdir(), key=lambda s: s.name)
        if sub.is_dir() and not sub.name.startswith(".")
    ]
    return {"success": True, "data": children, "error": None}


async def close_cc_session(
    session_id: str, registry: dict[str, CCHost] | None = None
) -> bool:
    """关闭 CC 会话，并在成功后移除其注册状态。

    未知会话返回 `False`，便于调用方重复清理。host 关闭失败时异常继续向上传递，
    registry、工作目录索引、显示名称和当前选择均保持不变。

    Args:
        session_id: 要关闭的 Trowel 会话 ID。
        registry: 要更新的 registry；为 `None` 时使用模块共享 registry。

    Returns:
        找到并成功关闭会话时返回 `True`；会话不存在时返回 `False`。

    Raises:
        BaseException: host 关闭失败时原样向上传递。
    """

    target_registry = _REGISTRY if registry is None else registry
    closed = await session_lifecycle.close_session(
        session_id,
        target_registry,
        workdir_index=_WORKDIR_INDEX,
        session_names=_SESSION_NAMES,
    )
    if closed and get_active_session_id() == session_id:
        set_active_session_id(None)
    return closed


def discard_unstarted_cc_session(
    session_id: str, registry: dict[str, CCHost] | None = None
) -> bool:
    """撤销 binding 提交失败的未启动 CC 会话。"""

    target_registry = _REGISTRY if registry is None else registry
    discarded = session_lifecycle.discard_unstarted_session(
        session_id,
        target_registry,
        workdir_index=_WORKDIR_INDEX,
        session_names=_SESSION_NAMES,
    )
    if discarded and get_active_session_id() == session_id:
        set_active_session_id(None)
    return discarded


@router.delete("/sessions/{sid}")
async def delete_session(
    sid: str,
    registry: dict[str, CCHost] = Depends(get_registry),
) -> dict:
    """关闭并移除已注册的 CC 会话；未知 id 返回 404。"""
    _require(sid, registry)  # HTTP 路由保留 404；facade 直接调用则返回 False。
    closed = await close_cc_session(sid, registry)
    return {"success": True, "data": {"closed": closed}, "error": None}
