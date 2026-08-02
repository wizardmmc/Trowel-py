"""Agent Host 的 HTTP 与 SSE 边界。"""

from __future__ import annotations

import json
import mimetypes
from collections.abc import Awaitable, Callable
from typing import Any, ParamSpec, TypeVar

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse

from trowel_py.agent_host.binding import Runtime
from trowel_py.agent_host.capabilities import (
    CC_CAPABILITIES,
    CODEX_CAPABILITIES,
)
from trowel_py.agent_host.hub import (
    InvalidSessionRequestError,
    RuntimeTurnError,
    RuntimeUnavailableError,
    SessionAccessError,
    SessionConflictError,
    SessionHub,
    SessionHubError,
    SessionNotFoundError,
    SessionOperationError,
)
from trowel_py.agent_host.local_files import (
    InvalidLocalFilePath,
    LocalFileAccessError,
    LocalFileNotFoundError,
    iter_file_chunks,
    open_local_file,
)
from trowel_py.agent_host.schemas import (
    AnswerAgentRequest,
    CreateAgentSessionRequest,
    GenerateAgentSessionTitleRequest,
    PatchAgentSessionRequest,
    RememberWorkspaceRequest,
    RenameAgentSessionRequest,
    SetCodexGoalRequest,
    SendMessageBody,
    StartCodexReviewRequest,
)
from trowel_py.agent_host.workspaces import (
    RecentWorkspaceStore,
    WorkspaceUnavailableError,
)

router = APIRouter()

_LOCAL_FILE_HEADERS = {
    "Cache-Control": "no-store",
    "Content-Security-Policy": (
        "sandbox allow-scripts; default-src 'none'; "
        "script-src 'unsafe-inline' 'unsafe-eval' https:; "
        "style-src 'unsafe-inline' https:; "
        "img-src data: blob: https:; font-src data: https:; "
        "media-src data: blob: https:; connect-src 'none'; "
        "form-action 'none'; base-uri 'none'"
    ),
    "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff",
}

P = ParamSpec("P")
T = TypeVar("T")

_HUB_ERROR_STATUS: tuple[tuple[type[SessionHubError], int], ...] = (
    (InvalidSessionRequestError, 400),
    (SessionAccessError, 403),
    (SessionNotFoundError, 404),
    (SessionConflictError, 409),
    (SessionOperationError, 422),
    (RuntimeTurnError, 502),
    (RuntimeUnavailableError, 503),
)


def _http_exception(exc: SessionHubError) -> HTTPException:
    """根据 Session Hub 错误的类型，生成带对应状态码的 HTTP 异常。

    Args:
        exc: Session Hub 执行会话操作时产生的错误。

    Returns:
        包含对应 HTTP 状态码和原错误说明的异常。

    Raises:
        TypeError: 该错误类型尚未配置对应的 HTTP 状态码。
    """

    for error_type, status_code in _HUB_ERROR_STATUS:
        if isinstance(exc, error_type):
            return HTTPException(status_code=status_code, detail=str(exc))
    raise TypeError(f"unmapped SessionHubError: {type(exc).__name__}")


def _call_hub(operation: Callable[P, T], *args: P.args, **kwargs: P.kwargs) -> T:
    """调用一个同步的 Session Hub 方法，并将其会话错误转换为 HTTP 异常。

    不是 SessionHubError 的异常不会在这里处理。

    Args:
        operation: 要调用的同步 Session Hub 方法。
        *args: 传给该方法的位置参数。
        **kwargs: 传给该方法的关键字参数。

    Returns:
        被调用方法的返回值。

    Raises:
        HTTPException: 被调用方法产生了能够转换的 Session Hub 错误。
        TypeError: Session Hub 错误尚未配置对应的 HTTP 状态码。
    """

    try:
        return operation(*args, **kwargs)
    except SessionHubError as exc:
        raise _http_exception(exc) from exc


async def _await_hub(
    operation: Callable[P, Awaitable[T]], *args: P.args, **kwargs: P.kwargs
) -> T:
    """调用并等待一个异步 Session Hub 方法，将其会话错误转换为 HTTP 异常。

    不是 SessionHubError 的异常不会在这里处理。

    Args:
        operation: 要调用的异步 Session Hub 方法。
        *args: 传给该方法的位置参数。
        **kwargs: 传给该方法的关键字参数。

    Returns:
        异步方法完成后的返回值。

    Raises:
        HTTPException: 被调用方法产生了能够转换的 Session Hub 错误。
        TypeError: Session Hub 错误尚未配置对应的 HTTP 状态码。
    """

    try:
        return await operation(*args, **kwargs)
    except SessionHubError as exc:
        raise _http_exception(exc) from exc


def get_hub(request: Request) -> SessionHub:
    """取得当前应用已经初始化的 Session Hub。

    Args:
        request: 当前 HTTP 请求，用于访问它所属的 FastAPI 应用。

    Returns:
        负责管理 Claude Code 和 Codex 会话的 Session Hub。

    Raises:
        HTTPException: Session Hub 尚未初始化，此时返回 503。
    """

    hub = getattr(request.app.state, "agent_hub", None)
    if hub is None:
        raise HTTPException(status_code=503, detail="agent hub not initialized")
    return hub


def get_workspace_store(request: Request) -> RecentWorkspaceStore:
    """取得当前应用已经初始化的 Recent 工作区仓储。

    Args:
        request: 当前 HTTP 请求，用于访问它所属的 FastAPI 应用。

    Returns:
        负责持久保存 Agent Recent 工作区的仓储。

    Raises:
        HTTPException: 仓储尚未初始化，此时返回 503。
    """

    store = getattr(request.app.state, "recent_workspace_store", None)
    if store is None:
        raise HTTPException(status_code=503, detail="workspace store not initialized")
    return store


def _sse(event: dict[str, Any]) -> bytes:
    """把一个会话事件编码成服务器推送事件（SSE）使用的数据帧。

    Args:
        event: 要发送给客户端的会话事件。

    Returns:
        包含事件 JSON 内容的 UTF-8 字节。
    """

    return f"data: {json.dumps(event, ensure_ascii=False)}\n\n".encode("utf-8")


@router.post("/sessions")
async def create_session(
    req: CreateAgentSessionRequest,
    hub: SessionHub = Depends(get_hub),
) -> dict:
    """创建指定 runtime 的会话；恢复请求会校验原生 id 的归属和冻结条件。"""

    req = await _await_hub(hub.prepare_create_request, req)
    explicit = req.model_fields_set
    if req.resume_from is not None:
        _call_hub(
            hub.validate_resume,
            Runtime(req.runtime),
            req.resume_from,
            memory_enabled=(
                req.memory_enabled if "memory_enabled" in explicit else None
            ),
            profile_enabled=(
                req.profile_enabled if "profile_enabled" in explicit else None
            ),
            self_enabled=req.self_enabled if "self_enabled" in explicit else None,
        )
    binding = _call_hub(hub.create, req)
    if req.resume_from is not None and req.runtime == "codex":
        try:
            binding = await _await_hub(hub.hydrate_resume, binding.session_id)
        except HTTPException:
            await hub.delete(binding.session_id)
            raise
    return {"success": True, "data": binding.to_dict(), "error": None}


@router.get("/session-defaults")
def get_session_defaults(hub: SessionHub = Depends(get_hub)) -> dict:
    """返回最近创建或使用的会话设置，供新建会话时预填。

    Args:
        hub: 用于读取最近会话设置的 Session Hub。

    Returns:
        统一响应。data 包含运行工具、模型、思考强度、权限以及 Memory 和 Profile
        开关；没有历史会话时，data 为 None。
    """

    return {
        "success": True,
        "data": hub.latest_session_defaults(),
        "error": None,
    }


@router.get("/workspaces/recent")
def list_recent_workspaces(
    store: RecentWorkspaceStore = Depends(get_workspace_store),
) -> dict:
    """按最近打开顺序返回工作区及其当前可用性。"""

    return {
        "success": True,
        "data": [workspace.to_dict() for workspace in store.list_recent()],
        "error": None,
    }


@router.post("/workspaces/recent")
def remember_workspace(
    req: RememberWorkspaceRequest,
    store: RecentWorkspaceStore = Depends(get_workspace_store),
) -> dict:
    """校验并记录用户确认打开的工作区。"""

    try:
        workspace = store.remember(req.path)
    except WorkspaceUnavailableError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "success": True,
        "data": workspace.to_dict(),
        "error": None,
    }


@router.get("/sessions/active")
def list_active(
    hub: SessionHub = Depends(get_hub),
) -> dict:
    """返回用户直接管理的会话，以及各会话的连接、处理和选中状态。

    列表不包含 Agent MCP 创建的委派子会话，但仍包含尚未连接或已经断开的用户
    会话。active_id 只表示工作台当前选中的用户会话，不表示该会话正在执行任务。

    Args:
        hub: 用于读取会话及实时状态的 Session Hub。

    Returns:
        统一响应。data.sessions 为用户会话列表，data.active_id 为当前选中的用户
        会话 ID；没有选中会话时为 None。
    """

    sessions, active_id = hub.list_active()
    return {
        "success": True,
        "data": {"sessions": sessions, "active_id": active_id},
        "error": None,
    }


@router.post("/sessions/{session_id}/activate")
def activate_session(
    session_id: str,
    hub: SessionHub = Depends(get_hub),
) -> dict:
    """把指定用户会话设为工作台当前选中的会话。

    此操作只改变选中状态，不会启动、中断或关闭任何会话。

    Args:
        session_id: 要切换到的会话 ID。
        hub: 用于更新当前选中会话的 Session Hub。

    Returns:
        统一响应。data.active_id 为切换后的会话 ID。

    Raises:
        HTTPException: 找不到指定会话时返回 404；指定会话不是用户会话时返回
            422。
    """

    active = _call_hub(hub.activate, session_id)
    return {"success": True, "data": {"active_id": active}, "error": None}


@router.get("/sessions/{session_id}")
def get_session(
    session_id: str,
    hub: SessionHub = Depends(get_hub),
) -> dict:
    """返回指定会话保存的 ID、运行工具和配置。

    此端点不会重新检查实际连接和处理状态；需要实时状态时应使用
    /sessions/active。

    Args:
        session_id: 要查询的会话 ID。
        hub: 用于读取会话信息的 Session Hub。

    Returns:
        统一响应。data 为保存的会话信息。

    Raises:
        HTTPException: 找不到指定会话，此时返回 404。
    """

    binding = hub.get(session_id)
    if binding is None:
        raise HTTPException(status_code=404, detail=f"session {session_id} not found")
    return {"success": True, "data": binding.to_dict(), "error": None}


@router.get("/sessions/{session_id}/files", response_class=StreamingResponse)
def get_session_file(
    session_id: str,
    path: str = Query(..., min_length=1),
    hub: SessionHub = Depends(get_hub),
) -> StreamingResponse:
    """读取指定会话项目目录中的一个文件，并分块发送给客户端。

    path 必须是项目目录内的相对路径。目录、符号链接以及无法确认位于项目目录内的
    路径都会被拒绝。响应会禁止缓存，并附带隔离页面内容的安全响应头。

    Args:
        session_id: 文件所属的会话 ID。
        path: 相对于会话项目目录的文件路径。
        hub: 用于读取会话项目目录的 Session Hub。

    Returns:
        文件流响应；内容类型根据文件名判断，无法判断时使用
        application/octet-stream。

    Raises:
        HTTPException: 路径格式无效时返回 400，无法保证访问范围时返回 403，
            会话或文件不存在时返回 404。
    """

    binding = hub.get(session_id)
    if binding is None:
        raise HTTPException(status_code=404, detail=f"session {session_id} not found")
    try:
        handle = open_local_file(binding.workdir, path)
    except InvalidLocalFilePath as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except LocalFileAccessError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except LocalFileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    media_type = mimetypes.guess_type(path)[0] or "application/octet-stream"
    return StreamingResponse(
        iter_file_chunks(handle),
        media_type=media_type,
        headers=_LOCAL_FILE_HEADERS,
    )


@router.patch("/sessions/{session_id}")
async def patch_session(
    session_id: str,
    body: PatchAgentSessionRequest,
    hub: SessionHub = Depends(get_hub),
) -> dict:
    """设置指定 Codex 会话下一轮使用的模型、思考强度或权限。

    运行工具创建后不能更换。未提供或值为 None 的字段保持不变。模型和思考强度
    会先暂存，在下一轮被 Codex 接受后才保存；权限选择会立即保存，但从下一轮起
    生效，且不能通过此端点改回 follow。

    Args:
        session_id: 要修改的会话 ID。
        body: 要修改的运行工具、模型、思考强度或权限；只处理非 None 字段。
        hub: 负责校验和暂存设置的 Session Hub。

    Returns:
        统一响应。data 包含最终选定的模型、思考强度和权限；adjusted 表示思考强度
        是否因模型不支持而被自动调整。没有字段需要处理时，data 为 None。

    Raises:
        HTTPException: 会话不存在时返回 404，当前状态不允许修改时返回 409，
            运行工具或设置无效时返回 422，Codex 当前不可用时返回 503。
    """

    _call_hub(
        hub.patch,
        session_id,
        runtime=body.runtime,
        model=body.model,
        effort=body.effort,
        permission_preset=body.permission_preset,
    )
    data: dict[str, Any] | None = None
    if body.model is not None or body.effort is not None:
        settings = await _await_hub(
            hub.update_codex_settings,
            session_id,
            model=body.model,
            effort=body.effort,
        )
        data = {**settings}
    if body.permission_preset is not None:
        permission = await _await_hub(
            hub.update_codex_permission,
            session_id,
            permission_preset=body.permission_preset,
        )
        data = {**(data or {}), **permission}
    return {"success": True, "data": data, "error": None}


@router.put("/sessions/{session_id}/title")
def rename_session_title(
    session_id: str,
    body: RenameAgentSessionRequest,
    hub: SessionHub = Depends(get_hub),
) -> dict:
    """保存用户手动指定的会话标题。

    Args:
        session_id: 要改名的用户会话 ID。
        body: 已去除首尾空白的非空标题。
        hub: 负责保存 binding 和原生会话标题索引的 Session Hub。

    Returns:
        统一响应。data 为更新后的完整会话记录。

    Raises:
        HTTPException: 会话不存在时返回 404，委派会话不能改名时返回 422。
    """

    binding = _call_hub(hub.rename_title, session_id, body.title)
    return {"success": True, "data": binding.to_dict(), "error": None}


@router.post("/sessions/{session_id}/title/generate")
async def generate_session_title(
    session_id: str,
    body: GenerateAgentSessionTitleRequest,
    hub: SessionHub = Depends(get_hub),
) -> dict:
    """保存首条提示词预览，并尝试异步生成语义标题。

    标题模型失败不会使主会话请求失败，此时 data 中保留 prompt 来源的预览。

    Args:
        session_id: 收到首条用户输入的用户会话 ID。
        body: 要概括而不执行的首条用户输入。
        hub: 负责标题降级、生成和竞争处理的 Session Hub。

    Returns:
        统一响应。data 为当前最终生效的完整会话记录。

    Raises:
        HTTPException: 会话不存在时返回 404，委派会话不能生成标题时返回 422。
    """

    binding = await _await_hub(hub.generate_title, session_id, body.text)
    return {"success": True, "data": binding.to_dict(), "error": None}


@router.delete("/sessions/{session_id}")
async def delete_session(
    session_id: str,
    hub: SessionHub = Depends(get_hub),
) -> dict:
    """移除指定会话，使 Trowel 不再显示或管理它。

    两种 runtime 都会先收敛活动 turn 和会话临时资源；原生历史不会删除。资源未能
    核验归零时保留 binding，并返回 ``needs_reconcile`` 供调用方重试。

    Args:
        session_id: 要移除的会话 ID。
        hub: 负责清理会话状态的 Session Hub。

    Returns:
        统一响应。data.status 区分 closed、needs_reconcile 和 not_found，并携带
        尚未关闭的资源数量、类型和去敏错误；closed 字段保留旧调用方兼容。
    """

    result = await hub.close_result(session_id)
    return {
        "success": True,
        "data": {
            "closed": result.status == "closed",
            "status": result.status,
            "remaining_resource_count": result.remaining_resource_count,
            "remaining_resource_kinds": list(result.remaining_resource_kinds),
            "error": result.error,
        },
        "error": None,
    }


@router.post("/sessions/{session_id}/interrupt")
async def interrupt_session(
    session_id: str,
    hub: SessionHub = Depends(get_hub),
) -> dict:
    """根据 binding 中断所属 runtime 的当前 turn。"""

    await _await_hub(hub.interrupt, session_id)
    return {"success": True, "data": {"interrupted": True}, "error": None}


@router.get("/sessions/{session_id}/requests")
async def list_session_requests(
    session_id: str,
    hub: SessionHub = Depends(get_hub),
) -> dict:
    """返回指定 Codex 会话的操作确认请求记录。

    操作确认请求是 Codex 在执行命令或修改文件前，请用户批准或拒绝的请求。结果既包含
    仍在等待决定的请求，也包含已经回答、超时、自动拒绝或因连接关闭而结束的请求，供
    客户端断线重连后恢复显示。Claude Code 会话返回空列表。

    Args:
        session_id: 要查询的会话 ID。
        hub: 用于读取操作确认请求的 Session Hub。

    Returns:
        统一响应。data.requests 为请求记录列表，每项包含请求类型、命令、可选决定、
        当前状态和最终决定等信息。

    Raises:
        HTTPException: 找不到指定会话时返回 404，Codex 当前不可用时返回 503。
    """

    requests = _call_hub(hub.list_requests, session_id)
    return {"success": True, "data": {"requests": requests}, "error": None}


@router.post("/sessions/{session_id}/requests/{request_id}/answer")
async def answer_session_request(
    session_id: str,
    request_id: str,
    body: AnswerAgentRequest,
    hub: SessionHub = Depends(get_hub),
) -> dict:
    """校验归属和 decision 后回答一个 connection-scoped Codex request。"""

    request = _call_hub(hub.answer_request, session_id, request_id, body.decision)
    return {
        "success": True,
        "data": {"answered": True, "request": request},
        "error": None,
    }


@router.get("/sessions/{session_id}/goal")
async def get_codex_goal(
    session_id: str,
    hub: SessionHub = Depends(get_hub),
) -> dict:
    """读取指定 Codex 会话设置的 Codex Goal。

    Codex Goal 是绑定在 Codex 对话线程上的持续任务记录，包含任务内容、当前状态、
    token 预算和用量等信息。尚未设置时返回 None。读取前会确保对应线程已经连接，
    并把实际使用的线程 ID、模型、思考强度和权限写回会话记录。Claude Code 会话
    不支持此功能。

    Args:
        session_id: 要查询的会话 ID。
        hub: 用于读取 Codex Goal 的 Session Hub。

    Returns:
        统一响应。data.goal 为完整的 Codex Goal；尚未设置时为 None。

    Raises:
        HTTPException: 找不到会话时返回 404；当前状态不允许连接 Codex 线程时返回
            409；会话由 Claude Code 运行时返回 422；连接线程、保存会话信息或读取
            Goal 失败时返回 502；Codex 当前不可用时返回 503。
    """

    goal = await _await_hub(hub.get_codex_goal, session_id)
    return {"success": True, "data": {"goal": goal}, "error": None}


@router.put("/sessions/{session_id}/goal")
async def set_codex_goal(
    session_id: str,
    body: SetCodexGoalRequest,
    hub: SessionHub = Depends(get_hub),
) -> dict:
    """创建指定 Codex 会话的 Codex Goal，或修改已有 Goal 中传入的字段。

    请求可以设置任务内容、当前状态和 token 数量上限，且至少要传入一个字段。没有传入
    的字段保留原值；将 token_budget 明确设为 None 表示取消 token 限制。操作前会确保
    对应的 Codex 对话线程已经连接，并保存实际使用的线程 ID 和设置。Claude Code 会话
    不支持此功能。

    Args:
        session_id: 要设置 Codex Goal 的会话 ID。
        body: 要创建或修改的 Goal 字段。
        hub: 负责设置 Codex Goal 的 Session Hub。

    Returns:
        统一响应。data.goal 为创建或更新后的完整 Codex Goal。

    Raises:
        HTTPException: 找不到会话时返回 404；没有提供任何字段、字段值无效或会话由
            Claude Code 运行时返回 422；连接线程、保存会话信息或设置 Goal 失败时
            返回 502；Codex 当前不可用时返回 503。
    """

    if not body.model_fields_set:
        raise HTTPException(status_code=422, detail="Goal update requires at least one field")
    goal = await _await_hub(
        hub.set_codex_goal,
        session_id,
        objective=body.objective,
        status=body.status,
        token_budget=body.token_budget,
        token_budget_supplied="token_budget" in body.model_fields_set,
    )
    return {"success": True, "data": {"goal": goal}, "error": None}


@router.delete("/sessions/{session_id}/goal")
async def clear_codex_goal(
    session_id: str,
    hub: SessionHub = Depends(get_hub),
) -> dict:
    """清除指定 Codex 会话当前设置的整个 Codex Goal。

    清除后该会话不再设置 Codex Goal；这不是把 Goal 状态改为 complete。操作前会确保
    对应的 Codex 对话线程已经连接，并保存实际使用的线程 ID 和设置。Claude Code
    会话不支持此功能。

    Args:
        session_id: 要清除 Codex Goal 的会话 ID。
        hub: 负责清除 Codex Goal 的 Session Hub。

    Returns:
        统一响应。data.cleared 表示 Codex 是否成功清除了 Goal。

    Raises:
        HTTPException: 找不到会话时返回 404；会话由 Claude Code 运行时返回 422；
            连接线程、保存会话信息或清除 Goal 失败时返回 502；Codex 当前不可用时
            返回 503。
    """

    cleared = await _await_hub(hub.clear_codex_goal, session_id)
    return {"success": True, "data": {"cleared": cleared}, "error": None}


@router.get("/sessions/{session_id}/commands")
async def list_codex_commands(
    session_id: str,
    hub: SessionHub = Depends(get_hub),
) -> dict:
    """列出指定 Codex 会话支持的命令及其使用条件。

    这些命令包括 /status、/compact 和 /review 等针对当前会话的操作，不是 shell
    命令，也不会作为普通用户消息发送给 Codex。只返回已经适配当前 Codex 命令行程序
    版本的命令；版本未知或尚未验证时返回空列表。

    Args:
        session_id: 要查询命令的 Codex 会话 ID。
        hub: 用于读取可用命令的 Session Hub。

    Returns:
        统一响应。data.commands 为命令列表，每项包含名称、说明、对应操作、来源，
        以及能否在 Codex 正在处理任务时使用。

    Raises:
        HTTPException: 找不到会话时返回 404；会话由 Claude Code 运行时返回 422；
            启动 Codex 或读取版本失败时返回 502；Codex 当前不可用时返回 503。
    """

    commands = await _await_hub(hub.list_codex_commands, session_id)
    return {"success": True, "data": {"commands": commands}, "error": None}


@router.post("/sessions/{session_id}/commands/compact")
async def compact_codex_session(
    session_id: str,
    hub: SessionHub = Depends(get_hub),
) -> dict:
    """请求 Codex 压缩指定会话当前对话线程的上下文。

    压缩会把较早的对话内容整理成更短的摘要，减少后续轮次需要携带的上下文。此操作
    不会发送普通用户消息。接口在 Codex 接受启动请求后立即返回，不等待压缩完成；
    后续进度和结果通过会话事件流发送。

    Args:
        session_id: 要压缩上下文的 Codex 会话 ID。
        hub: 负责启动上下文压缩的 Session Hub。

    Returns:
        统一响应。data.started 为 True 只表示 Codex 已接受启动请求，不表示压缩
        已经完成。

    Raises:
        HTTPException: 找不到会话时返回 404；当前有其他轮次正在运行时返回 409；
            会话由 Claude Code 运行时返回 422；连接会话或启动压缩失败时返回 502；
            Codex 当前不可用时返回 503。
    """

    await _await_hub(hub.compact_codex, session_id)
    return {"success": True, "data": {"started": True}, "error": None}


@router.post("/sessions/{session_id}/commands/review")
async def start_codex_review(
    session_id: str,
    body: StartCodexReviewRequest,
    hub: SessionHub = Depends(get_hub),
) -> dict:
    """在指定 Codex 会话的当前对话线程中启动一次代码审查。

    审查目标可以是未提交的改动、当前代码与指定基础分支的差异、某个提交，或一段
    自定义审查要求。审查不会创建普通用户消息，也不会另开对话线程。接口在 Codex
    接受启动请求后立即返回，不等待审查完成；后续过程和结果通过会话事件流发送。

    Args:
        session_id: 要启动代码审查的 Codex 会话 ID。
        body: 审查目标及其所需参数。
        hub: 负责启动 Codex 代码审查的 Session Hub。

    Returns:
        统一响应。data.review_thread_id 为审查所在的 Codex 对话线程 ID，
        data.turn_id 为本次审查的轮次 ID。

    Raises:
        HTTPException: 找不到会话时返回 404；当前有其他轮次正在运行时返回 409；
            审查目标无效或会话由 Claude Code 运行时返回 422；连接会话或启动审查
            失败时返回 502；Codex 当前不可用时返回 503。
    """

    result = await _await_hub(
        hub.start_codex_review,
        session_id,
        body.target.model_dump(exclude_none=True),
    )
    return {"success": True, "data": result, "error": None}


@router.get("/sessions/{session_id}/events")
def stream_codex_events(
    session_id: str,
    hub: SessionHub = Depends(get_hub),
) -> StreamingResponse:
    """持续向客户端发送指定 Codex 会话的实时事件。

    接口使用 SSE，也就是通过一条保持连接的 HTTP 响应不断发送事件。事件包括回复文本、
    工具调用、操作确认和状态变化等；一轮处理结束后连接仍保持，可继续接收后续轮次的
    事件。多个同时连接的客户端会各自收到相同事件，客户端断开也不会中断 Codex 当前
    正在执行的任务。

    此接口不负责重放历史事件，重新连接后不保证补发断线期间错过的内容。HTTP 响应开始
    发送后再发生的会话错误，会作为最后一条 error 事件返回。

    Args:
        session_id: 要接收实时事件的 Codex 会话 ID。
        hub: 负责订阅 Codex 事件的 Session Hub。

    Returns:
        持续发送 SSE 事件的响应。每条事件包含所属会话、事件类型、顺序编号和具体内容。

    Raises:
        HTTPException: 建立事件流前找不到会话时返回 404；会话由 Claude Code 运行时
            返回 422；Codex 当前不可用时返回 503。
    """

    # 在返回 200 前完成 runtime/归属检查。
    _call_hub(hub.require_codex_session, session_id)

    async def gen():
        """把订阅到的会话事件逐个编码为 SSE 数据帧，并在发生会话错误时用最后一帧报告错误。"""

        try:
            async for event in hub.subscribe_codex_events(session_id):
                yield _sse(event)
        except SessionHubError as exc:
            yield _sse(hub.error_envelope(session_id, exc))

    return StreamingResponse(gen(), media_type="text/event-stream")


@router.post("/sessions/{session_id}/turns")
async def start_codex_turn(
    session_id: str,
    body: SendMessageBody,
    hub: SessionHub = Depends(get_hub),
) -> dict:
    """向指定 Codex 会话发送一段文字，并启动新一轮处理。

    接口在 Codex 接受输入并创建轮次后立即返回，不等待这一轮结束。回复文本、工具调用
    和最终状态等后续事件通过 /events 接口发送；为了避免漏掉较早的事件，调用方应先
    建立事件流连接，再启动轮次。

    /compact、/review 等由 Trowel 单独处理的 Codex 命令不能作为普通文字发送，必须
    调用各自的接口。

    Args:
        session_id: 要启动新一轮处理的 Codex 会话 ID。
        body: 要发送给 Codex 的非空文字。
        hub: 负责启动 Codex 轮次的 Session Hub。

    Returns:
        统一响应。data.turn_id 为 Codex 创建的轮次 ID；返回该 ID 不表示本轮已经结束。

    Raises:
        HTTPException: 找不到会话时返回 404；当前已有轮次正在启动或运行时返回 409；
            输入为空、会话由 Claude Code 运行或输入是专用命令时返回 422；Codex
            未能接受输入或保存会话信息时返回 502；Codex 当前不可用时返回 503。
    """

    turn_id = await _await_hub(hub.start_codex_turn, session_id, body.text)
    return {
        "success": True,
        "data": {"turn_id": turn_id},
        "error": None,
    }


@router.post("/sessions/{session_id}/messages")
async def send_message(
    session_id: str,
    body: SendMessageBody,
    hub: SessionHub = Depends(get_hub),
) -> StreamingResponse:
    """发送一条消息并以 SSE 返回共享事件。

    未知会话、host 故障和 turn 启动失败都会转换为终止 ``error`` frame，保证流有
    明确结束信号。
    """

    async def gen():
        """把共享事件和运行错误编码为同一个 SSE 响应流。"""

        try:
            async for event in hub.stream(session_id, body.text):
                yield _sse(event)
        except SessionHubError as exc:
            yield _sse(hub.error_envelope(session_id, str(exc)))
        except Exception as exc:  # noqa: BLE001 - 转为终止 error frame
            yield _sse(hub.error_envelope(session_id, str(exc)))

    return StreamingResponse(gen(), media_type="text/event-stream")


@router.get("/runtimes")
def list_runtimes(
    hub: SessionHub = Depends(get_hub),
) -> dict:
    """列出 Claude Code 和 Codex 支持的功能及当前接入状态。

    connected 表示对应 CLI 已安装且 Host 已配置，不代表账号已登录或网络可用。

    Args:
        hub: 用于判断 Codex 会话管理器是否已配置的 Session Hub。

    Returns:
        统一响应。data 为两个运行工具的标识、显示名称、底层程序、功能列表和
        connected 状态。
    """

    runtimes = [
        {
            "runtime": "claude_code",
            "label": "Claude Code",
            "native": "claude -p (CCHost)",
            "capabilities": list(CC_CAPABILITIES),
            "connected": hub.runtime_available(Runtime.CLAUDE_CODE),
            "install_hint": "安装 Claude Code CLI 后重启 Trowel",
        },
        {
            "runtime": "codex",
            "label": "Codex",
            "native": "app-server (CodexHostManager)",
            "capabilities": list(CODEX_CAPABILITIES),
            "connected": hub.runtime_available(Runtime.CODEX),
            "install_hint": "安装 Codex CLI 后重启 Trowel",
        },
    ]
    return {"success": True, "data": runtimes, "error": None}


@router.get("/models")
async def list_models(
    hub: SessionHub = Depends(get_hub),
) -> dict:
    """返回当前 Codex 提供的模型目录。

    Trowel 不维护静态回退名单，模型及其思考强度选项按 Codex 返回的顺序提供。

    Args:
        hub: 用于读取 Codex 模型目录的 Session Hub。

    Returns:
        统一响应。data.models 为 Codex 当前提供的模型列表；Codex CLI 未安装时
        返回空列表。
    """

    if not hub.runtime_available(Runtime.CODEX):
        return {"success": True, "data": {"models": []}, "error": None}
    models = await _await_hub(hub.list_codex_models)
    return {"success": True, "data": {"models": models}, "error": None}


@router.get("/sessions/{session_id}/history")
async def get_session_history(
    session_id: str,
    hub: SessionHub = Depends(get_hub),
) -> dict:
    """回放指定 Claude Code 或 Codex 会话的历史事件。

    读取方式由会话记录中的运行工具决定，返回结果统一为 AgentEvent，并从 1 重新
    编号。会话尚未取得 Claude Code 会话 ID 或 Codex thread ID 时返回空列表。

    Args:
        session_id: 要回放历史的 Trowel 会话 ID。
        hub: 负责读取并统一历史事件的 Session Hub。

    Returns:
        统一响应。data 为按时间顺序排列的历史事件列表。

    Raises:
        HTTPException: 找不到会话时返回 404；Codex 会话管理器未配置时返回 503。
    """

    envelopes = await _await_hub(hub.history, session_id)
    return {"success": True, "data": envelopes, "error": None}


@router.get("/sessions/{session_id}/subagents/{thread_id}/history")
async def get_subagent_history(
    session_id: str,
    thread_id: str,
    hub: SessionHub = Depends(get_hub),
) -> dict:
    """读取指定 Codex 子 Agent 对话线程自己产生的历史事件。

    读取前会沿父线程关系确认该线程以当前会话的主线程为根，并排除各级父线程已有的
    轮次。返回事件统一为 AgentEvent，并从 1 重新编号。

    Args:
        session_id: 子线程所属的 Trowel 会话 ID。
        thread_id: 要回放的 Codex 子线程 ID。
        hub: 负责校验归属并读取子线程历史的 Session Hub。

    Returns:
        统一响应。data 为该子 Agent 自己产生的历史事件列表。

    Raises:
        HTTPException: 子线程不属于指定会话时返回 403；找不到会话或主线程时返回
            404；会话由 Claude Code 运行时返回 422；Codex 会话管理器未配置时
            返回 503。
    """

    envelopes = await _await_hub(hub.child_history, session_id, thread_id)
    return {"success": True, "data": envelopes, "error": None}


@router.get("/sessions")
async def list_history(
    workdir: str = Query(..., min_length=1),
    limit: int = Query(20, ge=1, le=100),
    cursor: str | None = Query(None),
    hub: SessionHub = Depends(get_hub),
) -> dict:
    """分页列出指定工作目录中的 Claude Code 和 Codex 历史会话。

    两种运行工具的记录合并后按更新时间从新到旧排列。未配置 Codex 会话管理器时
    只返回 Claude Code 记录。

    Args:
        workdir: 要查询历史会话的工作目录。
        limit: 本页最多返回的会话数，范围为 1 到 100。
        cursor: 上一页返回的下一页游标；首次查询时为 None。
        hub: 负责读取、合并并分页历史会话的 Session Hub。

    Returns:
        统一响应。data 为本页历史会话；meta.next_cursor 为下一页游标，没有下一页
        时为 None。

    Raises:
        HTTPException: 分页游标无效时返回 400。
    """

    rows, next_cursor = await _await_hub(
        hub.list_history,
        workdir,
        limit=limit,
        cursor=cursor,
    )
    return {
        "success": True,
        "data": rows,
        "meta": {"limit": limit, "next_cursor": next_cursor},
        "error": None,
    }
