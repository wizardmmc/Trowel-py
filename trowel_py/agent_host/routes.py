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
from trowel_py.agent_host.cc_adapter import CcEventAdapter
from trowel_py.agent_host.hub import (
    CC_CAPABILITIES,
    CODEX_CAPABILITIES,
    CrossRuntimeResumeError,
    RuntimeFrozenError,
    SessionHub,
)
from trowel_py.agent_host.schemas import (
    AnswerAgentRequest,
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
        raise HTTPException(status_code=404, detail=f"session {session_id} not found")
    return {"success": True, "data": binding.to_dict(), "error": None}


@router.patch("/sessions/{session_id}")
async def patch_session(
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
    data = None
    if body.model is not None or body.effort is not None:
        data = await hub.update_codex_settings(
            session_id, model=body.model, effort=body.effort
        )
    return {"success": True, "data": data, "error": None}


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


@router.get("/sessions/{session_id}/requests")
async def list_session_requests(
    session_id: str,
    hub: SessionHub = Depends(get_hub),
) -> dict:
    """List retained request states so a short frontend disconnect can recover."""

    requests = hub.list_requests(session_id)
    return {"success": True, "data": {"requests": requests}, "error": None}


@router.post("/sessions/{session_id}/requests/{request_id}/answer")
async def answer_session_request(
    session_id: str,
    request_id: str,
    body: AnswerAgentRequest,
    hub: SessionHub = Depends(get_hub),
) -> dict:
    """Validate and answer one connection-scoped Codex server request."""

    request = hub.answer_request(session_id, request_id, body.decision)
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
    """Send one message and stream host events back as SSE.

    Any error (unknown session, host down, turn-start failure) is converted to
    a terminal ``error`` SSE frame so the client always gets a closing signal.
    """

    async def gen():
        try:
            async for event in hub.stream(session_id, body.text):
                yield _sse(event)
        except HTTPException as exc:
            yield _sse(hub.error_envelope(session_id, exc.detail))
        except Exception as exc:  # noqa: BLE001 — surface as terminal error frame
            yield _sse(hub.error_envelope(session_id, str(exc)))

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


@router.get("/models")
async def list_models(
    hub: SessionHub = Depends(get_hub),
) -> dict:
    """Return the visible native Codex model catalog without static fallback."""

    models = await hub.list_codex_models()
    return {"success": True, "data": {"models": models}, "error": None}


@router.get("/sessions/{session_id}/history")
def get_session_history(
    session_id: str,
    hub: SessionHub = Depends(get_hub),
) -> dict:
    """Replay a session's stored history as AgentEvent v1 envelopes (slice-074).

    CC history comes from cc_host's on-disk jsonl scan, wrapped through a fresh
    :class:`CcEventAdapter` (seq starts at 1 — history is a new stream from the
    reducer's view, not a continuation of a live one). The CC ``native_session_id``
    must already be written back (i.e. the session completed at least one turn);
    a fresh session with no native id returns an empty list.

    Codex thread history replay lands in slice-079; this slice returns a clear
    501 so the frontend can fall back to its existing skip-non-CC behaviour.

    Raises:
        HTTPException: 404 unknown session; 501 Codex (slice-079).
    """

    binding = hub.get(session_id)
    if binding is None:
        raise HTTPException(status_code=404, detail=f"session {session_id} not found")
    if binding.runtime is Runtime.CODEX:
        raise HTTPException(
            status_code=501,
            detail="codex thread history replay lands in slice-079",
        )
    native_session_id = binding.native_session_id
    if not native_session_id:
        return {"success": True, "data": [], "error": None}
    from trowel_py.cc_host.history import parse_history

    events = parse_history(binding.workdir, native_session_id)
    # Fresh adapter: history is an independent stream (seq from 1), never a
    # continuation of the live adapter's counter.
    adapter = CcEventAdapter(session_id)
    envelopes = [adapter.wrap(e.model_dump()).model_dump(by_alias=True) for e in events]
    return {"success": True, "data": envelopes, "error": None}


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
