"""HTTP + SSE endpoints for the host-neutral Session Hub (slice-072).

Thin wrapper around :class:`SessionHub` — no business logic here, only request
parsing, runtime/cross-resume error → HTTP status mapping, and SSE framing for
the message stream. Tests override :func:`get_hub` to inject a Hub wired to
fakes (spec C-7: route tests never touch the real store or DB).
"""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse

from trowel_py.agent_host.binding import Runtime
from trowel_py.agent_host.hub import (
    CC_CAPABILITIES,
    CODEX_CAPABILITIES,
    CrossRuntimeResumeError,
    RuntimeFrozenError,
    SessionHub,
)
from trowel_py.agent_host.schemas import (
    CreateAgentSessionRequest,
    PatchAgentSessionRequest,
    SendMessageBody,
)

router = APIRouter()


def get_hub(request: Request) -> SessionHub:
    """Resolve the app-wide SessionHub (override in tests)."""

    hub = getattr(request.app.state, "agent_hub", None)
    if hub is None:
        raise HTTPException(status_code=503, detail="agent hub not initialized")
    return hub


def _sse(event: dict[str, Any]) -> bytes:
    """Serialize one event dict as an SSE data frame."""

    return f"data: {json.dumps(event, ensure_ascii=False)}\n\n".encode("utf-8")


def _error_event(detail: Any) -> dict[str, Any]:
    """Build a terminal error frame so a stream always ends with a signal."""

    return {"type": "error", "subclass": "host_error", "errors": [str(detail)]}


@router.post("/sessions")
def create_session(
    req: CreateAgentSessionRequest,
    request: Request,
    hub: SessionHub = Depends(get_hub),
) -> dict:
    """Create a session under the chosen runtime (resume validated C-2)."""

    if req.resume_from is not None:
        try:
            hub.validate_resume(Runtime(req.runtime), req.resume_from)
        except CrossRuntimeResumeError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
    binding = hub.create(req, request)
    return {"success": True, "data": binding.to_dict(), "error": None}


@router.get("/sessions/active")
def list_active(
    hub: SessionHub = Depends(get_hub),
) -> dict:
    """List every live session (mixed CC + Codex) + the active id."""

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
    """Switch the active (viewed) session — view state only (C-4)."""

    active = hub.activate(session_id)
    return {"success": True, "data": {"active_id": active}, "error": None}


@router.get("/sessions/{session_id}")
def get_session(
    session_id: str,
    hub: SessionHub = Depends(get_hub),
) -> dict:
    """Return one session's binding."""

    binding = hub.get(session_id)
    if binding is None:
        raise HTTPException(
            status_code=404, detail=f"session {session_id} not found"
        )
    return {"success": True, "data": binding.to_dict(), "error": None}


@router.patch("/sessions/{session_id}")
def patch_session(
    session_id: str,
    body: PatchAgentSessionRequest,
    hub: SessionHub = Depends(get_hub),
) -> dict:
    """Partial update. slice-072: a runtime change → 422 (C-1)."""

    try:
        hub.patch(
            session_id, runtime=body.runtime, model=body.model, effort=body.effort
        )
    except RuntimeFrozenError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"success": True, "data": None, "error": None}


@router.delete("/sessions/{session_id}")
async def delete_session(
    session_id: str,
    hub: SessionHub = Depends(get_hub),
) -> dict:
    """Close the native host + drop the binding (idempotent)."""

    closed = await hub.delete(session_id)
    return {"success": True, "data": {"closed": closed}, "error": None}


@router.post("/sessions/{session_id}/interrupt")
async def interrupt_session(
    session_id: str,
    hub: SessionHub = Depends(get_hub),
) -> dict:
    """Interrupt the running turn, routed by binding."""

    await hub.interrupt(session_id)
    return {"success": True, "data": {"interrupted": True}, "error": None}


@router.post("/sessions/{session_id}/messages")
async def send_message(
    session_id: str,
    body: SendMessageBody,
    hub: SessionHub = Depends(get_hub),
) -> StreamingResponse:
    """Send one message and stream host events back as SSE.

    Any error (unknown session, host down, turn-start failure) is converted to
    a terminal ``error`` SSE frame so the client always gets a closing signal.
    """

    async def gen():
        try:
            async for event in hub.stream(session_id, body.text):
                yield _sse(event)
        except HTTPException as exc:
            yield _sse(_error_event(exc.detail))
        except Exception as exc:  # noqa: BLE001 — surface as terminal error frame
            yield _sse(_error_event(str(exc)))

    return StreamingResponse(gen(), media_type="text/event-stream")


@router.get("/runtimes")
def list_runtimes(
    hub: SessionHub = Depends(get_hub),
) -> dict:
    """Return the two runtimes with capabilities + connection status.

    Codex ``connected`` reflects whether the shared manager is wired; the
    deeper login/proxy diagnostics land in slice-080.
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


@router.get("/sessions")
def list_history(
    workdir: str = Query(..., min_length=1),
    hub: SessionHub = Depends(get_hub),
) -> dict:
    """Mixed history: CC jsonl rows + Codex bindings, each tagged by runtime.

    CC rows come from cc_host's on-disk scan; Codex rows come from this hub's
    binding store (a trowel-created Codex thread trowel can resume). Each row
    carries the runtime + native id so a resume request is routed correctly
    (spec pass-criterion: mixed history rows show runtime, resume carries it).
    """

    from trowel_py.cc_host.session_scan import list_sessions as cc_list_sessions

    rows: list[dict[str, Any]] = []
    for summary in cc_list_sessions(workdir, limit=20):
        rows.append(
            {
                "runtime": "claude_code",
                "native_session_id": summary.cc_session_id,
                "title": summary.title,
                "updated_at": summary.updated_at,
            }
        )
    for binding in hub.store.list_all():
        if binding.runtime is Runtime.CODEX and binding.workdir == workdir:
            rows.append(
                {
                    "runtime": "codex",
                    "native_session_id": binding.native_session_id,
                    "title": binding.name,
                    "updated_at": binding.updated_at,
                }
            )
    return {"success": True, "data": rows, "error": None}
