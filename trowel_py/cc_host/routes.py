"""HTTP + SSE endpoints for the CC host (slice022).

Endpoints:
    POST   /api/cc/sessions                  open or resume a session
    POST   /api/cc/sessions/{id}/messages    send a message, stream trowel events (SSE)
    POST   /api/cc/sessions/{id}/interrupt   SIGINT the current turn
    GET    /api/cc/sessions?workdir=...      list resumable CC history sessions
    GET    /api/cc/sessions/{id}/history     replay a resumed session's stored events
    DELETE /api/cc/sessions/{id}             kill and drop the session

This is trowel's first async streaming route. Sessions live in an in-memory
registry (stateless host layer: nothing persists across server restarts).
"""

from __future__ import annotations

import uuid
from dataclasses import asdict
from pathlib import Path
from typing import AsyncIterator

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse

from trowel_py.cc_host import checkpoint
from trowel_py.cc_host.history import parse_history
from trowel_py.cc_host.service import CCHost
from trowel_py.cc_host.session_scan import count_sessions, list_sessions
from trowel_py.schemas.cc_host import (
    AnswerElicitRequest,
    CreateSessionRequest,
    ErrorEvent,
    RevertRequest,
    SendMessageRequest,
)

router = APIRouter()

# The history dropdown only surfaces the most recent sessions — returning all
# (often 200+) makes the UI dropdown unwieldy and blew up the playwright MCP
# accessibility snapshot during e2e. Cap the list; full history stays on disk.
_HISTORY_DROPDOWN_LIMIT = 10

# In-memory session store. Module-level on purpose: the host layer is stateless
# (no DB) — a server restart drops every session. Override via get_registry().
_REGISTRY: dict[str, CCHost] = {}


def get_registry() -> dict[str, CCHost]:
    """Return the session registry (overridable in tests)."""
    return _REGISTRY


def _require(sid: str, registry: dict[str, CCHost]) -> CCHost:
    """Look up a session or raise 404.

    Args:
        sid: trowel session id.
        registry: the session registry.

    Returns:
        the CCHost for the session.

    Raises:
        HTTPException: 404 if the session id is not in the registry.
    """
    host = registry.get(sid)
    if host is None:
        raise HTTPException(status_code=404, detail=f"session {sid} not found")
    return host


def _sse(event: object) -> str:
    """Serialize one trowel event as an SSE data frame."""
    return f"data: {event.model_dump_json()}\n\n"  # type: ignore[attr-defined]


@router.post("/sessions")
def create_session(
    req: CreateSessionRequest,
    registry: dict[str, CCHost] = Depends(get_registry),
) -> dict:
    """Open a new session (optionally resuming a CC session id)."""
    if not Path(req.workdir).is_dir():
        raise HTTPException(status_code=400, detail="workdir does not exist")
    sid = uuid.uuid4().hex
    host = CCHost(
        sid,
        req.workdir,
        model=req.model,
        effort=req.effort,
        permission_mode=req.permission_mode,
        resume_from=req.resume_from,
    )
    registry[sid] = host
    return {
        "success": True,
        "data": {
            "session_id": sid,
            "cc_session_id": req.resume_from,
            "model": host.model,
            # slice-026: whether reverting turns is supported for this workdir
            # (non-git workdirs get the banner + no revert buttons in the UI).
            "revert_enabled": checkpoint.is_git_repo(req.workdir),
        },
        "error": None,
    }


@router.post("/sessions/{sid}/messages")
async def send_message(
    sid: str,
    body: SendMessageRequest,
    registry: dict[str, CCHost] = Depends(get_registry),
) -> StreamingResponse:
    """Send one message and stream trowel events back as SSE."""
    host = _require(sid, registry)

    async def gen() -> AsyncIterator[bytes]:
        """Yield each trowel event from host.send() as an SSE data frame.

        Any exception mid-stream is converted to an ErrorEvent frame so the
        client always gets a terminal signal.
        """
        try:
            async for event in host.send(body.text):
                yield _sse(event).encode()
        except Exception as exc:  # noqa: BLE001 — surface stream errors as events
            yield _sse(
                ErrorEvent(type="error", subclass="host_error", errors=[str(exc)])
            ).encode()

    return StreamingResponse(gen(), media_type="text/event-stream")


@router.post("/sessions/{sid}/interrupt")
async def interrupt(
    sid: str,
    registry: dict[str, CCHost] = Depends(get_registry),
) -> dict:
    """SIGINT the current turn of a session."""
    host = _require(sid, registry)
    await host.interrupt()
    return {"success": True, "data": {"interrupted": True}, "error": None}


@router.post("/sessions/{sid}/answer")
async def answer_elicit(
    sid: str,
    body: AnswerElicitRequest,
    registry: dict[str, CCHost] = Depends(get_registry),
) -> dict:
    """Answer (or cancel) the pending AskUserQuestion elicitation (slice-025-c).

    Writes control_response(allow + updatedInput.answers) or control_response
    (deny) to cc stdin so cc unblocks and continues the turn.
    """
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
    """Revert a turn: git-restore the worktree + truncate the CC jsonl (slice-026 E1).

    Looks up the checkpoint for ``turn_id`` (dropping that turn and every later
    one), restores the worktree from the snapshot, truncates the CC session
    jsonl back to the recorded offset, then kills the live CC process so the
    next send() re-resumes from the truncated history.

    Raises:
        404: session or checkpoint not found.
        400: workdir is not a git repo (revert unsupported).
    """
    host = _require(sid, registry)
    try:
        meta = checkpoint.revert(host.workdir, body.turn_id)
    except checkpoint.NotAGitRepoError:
        raise HTTPException(status_code=400, detail="workdir is not a git repo")
    except checkpoint.UnknownCheckpointError:
        raise HTTPException(status_code=404, detail=f"checkpoint {body.turn_id} not found")
    # Drop the in-memory CC process so --resume reloads the truncated jsonl.
    await host.reload()
    return {
        "success": True,
        "data": {
            "reverted_turn_id": body.turn_id,
            "jsonl_path": meta.jsonl_path,
            "jsonl_offset": meta.jsonl_offset,
        },
        "error": None,
    }


@router.get("/sessions")
def list_history(
    workdir: str = Query(..., min_length=1),
) -> dict:
    """List the most recent resumable CC history sessions for a workdir.

    Returns the N most recent sessions (any age, not just trowel-created) for
    the history dropdown — capped to keep the dropdown usable. ``meta.total``
    is the true on-disk count so the UI can show "共 N · 最近 M" instead of
    misleadingly implying only N sessions exist.
    """
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
    """Replay a session's stored CC history as trowel events.

    Reads the CC session jsonl for the session's cc_session_id + workdir and
    returns normalized trowel events isomorphic to the live stream (plus a
    UserEvent per historical user turn), so the frontend renders history and
    live with one reducer. Empty list when the session has no cc_session_id
    yet or the jsonl is absent.
    """
    host = _require(sid, registry)
    cc_session_id = host.cc_session_id
    if not cc_session_id:
        return {"success": True, "data": [], "error": None}
    events = [e.model_dump() for e in parse_history(host.workdir, cc_session_id)]
    return {"success": True, "data": events, "error": None}


@router.delete("/sessions/{sid}")
async def delete_session(
    sid: str,
    registry: dict[str, CCHost] = Depends(get_registry),
) -> dict:
    """Kill the subprocess and remove the session from the registry."""
    host = _require(sid, registry)
    await host.close()
    registry.pop(sid, None)
    return {"success": True, "data": {"closed": True}, "error": None}
