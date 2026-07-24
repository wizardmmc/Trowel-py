"""提供 Claude Code 会话的 HTTP 与 SSE 兼容路由。

本模块持有进程内 session registry、多开索引和当前选择；会话历史与 checkpoint
仍保存在磁盘。生命周期算法位于 session_lifecycle，公开 facade 保留原有依赖边界。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import AsyncIterator

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse

from trowel_py.cc_host import checkpoint
from trowel_py.cc_host import session_lifecycle
from trowel_py.cc_host.history import parse_history
from trowel_py.cc_host.models import list_models
from trowel_py.cc_host.service import CCHost
from trowel_py.cc_host.session_scan import count_sessions, list_sessions
from trowel_py.cc_host.slash_items import list_slash_items
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

_WORKDIR_INDEX: dict[str, set[str]] = {}   # workdir → {sid}（命名序号 + 按 workdir 查询）
_SESSION_NAMES: dict[str, str] = {}         # sid → 显示名（basename + #N）
_ACTIVE_SID: str | None = None              # 当前活跃 session（多开切换）
# MAX_RUNNING 仅保留公开兼容；当前路由只执行连接数门禁。
MAX_RUNNING = 5
MAX_CONNECTIONS = 20                        # 已创建 session 总数上限


def get_registry() -> dict[str, CCHost]:
    """返回 FastAPI Depends 与 Agent Hub 共享的进程内 registry。"""

    return _REGISTRY


def get_active_session_id() -> str | None:
    return _ACTIVE_SID


def set_active_session_id(session_id: str | None) -> None:
    """更新当前会话；Agent Hub 通过此入口同步 CC 选择。"""

    global _ACTIVE_SID
    _ACTIVE_SID = session_id


def _require(sid: str, registry: dict[str, CCHost]) -> CCHost:
    """返回已注册 host；未知 id 在进入流或执行副作用前抛出 404。"""

    host = registry.get(sid)
    if host is None:
        raise HTTPException(status_code=404, detail=f"session {sid} not found")
    return host


def _sse(event: object) -> str:
    return f"data: {event.model_dump_json()}\n\n"  # type: ignore[attr-defined]


@dataclass(frozen=True)
class OpenedCcSession:
    """已经注册的 CC 会话。"""

    sid: str
    host: CCHost
    name: str


def open_cc_session(
    req: CreateSessionRequest,
    request: Request,
    registry: dict[str, CCHost] | None = None,
) -> OpenedCcSession:
    """创建并注册 CC 会话，保留 routes 的状态与依赖注入边界。

    内核在创建时延迟导入 MCP 配置写入器；仅 memory 开启时写配置，关闭时
    传入 None。校验或 host 构造失败时不会写入 registry 与派生索引。
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
) -> OpenedCcSession:
    """用显式启动配置创建 CC 会话，供非 HTTP 编排层调用。"""

    target_registry = _REGISTRY if registry is None else registry
    sid, host, name = session_lifecycle.open_session(
        req,
        target_registry,
        proxy_base_url=proxy_base_url,
        settings_path=settings_path,
        workdir_index=_WORKDIR_INDEX,
        session_names=_SESSION_NAMES,
        max_connections=MAX_CONNECTIONS,
        host_factory=CCHost,
    )
    set_active_session_id(sid)
    return OpenedCcSession(sid=sid, host=host, name=name)


@router.post("/sessions")
def create_session(
    req: CreateSessionRequest,
    request: Request,
    registry: dict[str, CCHost] = Depends(get_registry),
) -> dict:
    """创建新的 CC 会话，可通过原生会话 id 恢复已有会话。"""
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
    connected 表示是否仍有存活的 CC 子进程。"""
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
    return {"success": ok, "data": {"answered": ok}, "error": None if ok else "no_pending_elicit"}


@router.post("/sessions/{sid}/revert")
async def revert_turn(
    sid: str,
    body: RevertRequest,
    registry: dict[str, CCHost] = Depends(get_registry),
) -> dict:
    """恢复指定 turn 前的工作树与 CC 历史，并结束当前进程以便后续恢复。
    未知 checkpoint 返回 404，非 Git 工作目录返回 400。"""
    host = _require(sid, registry)
    try:
        meta = checkpoint.revert(host.workdir, body.turn_id)
    except checkpoint.NotAGitRepoError:
        raise HTTPException(status_code=400, detail="workdir is not a git repo")
    except checkpoint.UnknownCheckpointError:
        raise HTTPException(status_code=404, detail=f"checkpoint {body.turn_id} not found")
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
    workdir: str = Query(..., min_length=1),
) -> dict:
    """列出工作目录最近 10 个可恢复 CC 会话，并在 meta.total 返回磁盘总数。"""
    items = [asdict(s) for s in list_sessions(workdir, limit=_HISTORY_DROPDOWN_LIMIT)]
    return {
        "success": True,
        "data": items,
        "error": None,
        "meta": {"total": count_sessions(workdir), "limit": _HISTORY_DROPDOWN_LIMIT},
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


def _init_roster_for_workdir(
    workdir: str, registry: dict[str, CCHost]
) -> list[str]:
    """返回 workdir 的初始化命令表，优先使用该目录的 active session。

    active 不属于该 workdir 或无可用 roster 时，回退到同目录其他 session。
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
    """关闭已注册 CC 会话并清理多开状态。

    直接调用对未知 id 返回 False，供 Agent Hub 幂等清理；DELETE 路由会先经
    ``_require`` 转为 404。host.close 失败时保留 registry、索引与 active 状态。
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


@router.delete("/sessions/{sid}")
async def delete_session(
    sid: str,
    registry: dict[str, CCHost] = Depends(get_registry),
) -> dict:
    """关闭并移除已注册的 CC 会话；未知 id 返回 404。"""
    _require(sid, registry)  # HTTP 路由保留 404；facade 直接调用则返回 False。
    closed = await close_cc_session(sid, registry)
    return {"success": True, "data": {"closed": closed}, "error": None}
