"""Agent Host 的 HTTP 与 SSE 边界。"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from typing import Any, ParamSpec, TypeVar

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse

from trowel_py.agent_host.binding import Runtime
from trowel_py.agent_host.hub import (
    CC_CAPABILITIES,
    CODEX_CAPABILITIES,
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
from trowel_py.agent_host.schemas import (
    AnswerAgentRequest,
    CreateAgentSessionRequest,
    PatchAgentSessionRequest,
    SendMessageBody,
)

router = APIRouter()

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
    for error_type, status_code in _HUB_ERROR_STATUS:
        if isinstance(exc, error_type):
            return HTTPException(status_code=status_code, detail=str(exc))
    raise TypeError(f"unmapped SessionHubError: {type(exc).__name__}")


def _call_hub(operation: Callable[P, T], *args: P.args, **kwargs: P.kwargs) -> T:
    try:
        return operation(*args, **kwargs)
    except SessionHubError as exc:
        raise _http_exception(exc) from exc


async def _await_hub(
    operation: Callable[P, Awaitable[T]], *args: P.args, **kwargs: P.kwargs
) -> T:
    try:
        return await operation(*args, **kwargs)
    except SessionHubError as exc:
        raise _http_exception(exc) from exc


def get_hub(request: Request) -> SessionHub:
    hub = getattr(request.app.state, "agent_hub", None)
    if hub is None:
        raise HTTPException(status_code=503, detail="agent hub not initialized")
    return hub


def _sse(event: dict[str, Any]) -> bytes:
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
    """返回最近成功创建或使用的 runtime 配置；无历史时返回 null。"""

    return {
        "success": True,
        "data": hub.latest_session_defaults(),
        "error": None,
    }


@router.get("/sessions/active")
def list_active(
    hub: SessionHub = Depends(get_hub),
) -> dict:
    """返回持久化会话、基于本地 registry/manager 的状态和当前视图 id。"""

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
    """只切换当前视图，不关闭或中断任何会话。"""

    active = _call_hub(hub.activate, session_id)
    return {"success": True, "data": {"active_id": active}, "error": None}


@router.get("/sessions/{session_id}")
def get_session(
    session_id: str,
    hub: SessionHub = Depends(get_hub),
) -> dict:
    """返回单个持久化 binding；未知 session id 返回 404。"""

    binding = hub.get(session_id)
    if binding is None:
        raise HTTPException(status_code=404, detail=f"session {session_id} not found")
    return {"success": True, "data": binding.to_dict(), "error": None}


@router.patch("/sessions/{session_id}")
async def patch_session(
    session_id: str,
    body: PatchAgentSessionRequest,
    hub: SessionHub = Depends(get_hub),
) -> dict:
    """拒绝修改 runtime；model/effort 仅为下一次 Codex turn 排队。"""

    _call_hub(
        hub.patch,
        session_id,
        runtime=body.runtime,
        model=body.model,
        effort=body.effort,
    )
    data = None
    if body.model is not None or body.effort is not None:
        data = await _await_hub(
            hub.update_codex_settings,
            session_id,
            model=body.model,
            effort=body.effort,
        )
    return {"success": True, "data": data, "error": None}


@router.delete("/sessions/{session_id}")
async def delete_session(
    session_id: str,
    hub: SessionHub = Depends(get_hub),
) -> dict:
    """注销原生会话并删除 binding；重复删除仍成功返回。"""

    closed = await hub.delete(session_id)
    return {"success": True, "data": {"closed": closed}, "error": None}


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
    """返回保留中的 Codex request，使短暂断线后仍可恢复待决策状态。"""

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
    """返回两种 runtime 的 capability 与 host 可用状态。

    Codex ``connected`` 只表示共享 manager 已装配，不代表登录或代理健康。
    """

    runtimes = [
        {
            "runtime": "claude_code",
            "label": "Claude Code",
            "native": "claude -p (CCHost)",
            "capabilities": list(CC_CAPABILITIES),
            "connected": True,
        },
        {
            "runtime": "codex",
            "label": "Codex",
            "native": "app-server (CodexHostManager)",
            "capabilities": list(CODEX_CAPABILITIES),
            "connected": hub.codex_available,
        },
    ]
    return {"success": True, "data": runtimes, "error": None}


@router.get("/models")
async def list_models(
    hub: SessionHub = Depends(get_hub),
) -> dict:
    """返回原生 Codex model catalog，不维护静态回退名单。"""

    models = await _await_hub(hub.list_codex_models)
    return {"success": True, "data": {"models": models}, "error": None}


@router.get("/sessions/{session_id}/history")
async def get_session_history(
    session_id: str,
    hub: SessionHub = Depends(get_hub),
) -> dict:
    """按 binding runtime 回放原生 history；序号独立从 1 开始。"""

    envelopes = await _await_hub(hub.history, session_id)
    return {"success": True, "data": envelopes, "error": None}


@router.get("/sessions")
async def list_history(
    workdir: str = Query(..., min_length=1),
    limit: int = Query(20, ge=1, le=100),
    cursor: str | None = Query(None),
    hub: SessionHub = Depends(get_hub),
) -> dict:
    """返回两个 runtime 全局排序后的历史分页。"""

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
