"""HTTP + SSE endpoints for the CC host (slice022).

Endpoints:
    POST   /api/cc/sessions                  open or resume a session
    POST   /api/cc/sessions/{id}/messages    send a message, stream trowel events (SSE)
    POST   /api/cc/sessions/{id}/interrupt   SIGINT the current turn
    GET    /api/cc/sessions?workdir=...      list resumable CC history sessions
    GET    /api/cc/sessions/{id}/history     replay a resumed session's stored events
    GET    /api/cc/models                    list cc model aliases (slice-027 C2)
    GET    /api/cc/slash-items?workdir=...   list slash commands+skills (slice-027 C1)
    DELETE /api/cc/sessions/{id}             kill and drop the session

This is trowel's first async streaming route. Sessions live in an in-memory
registry (stateless host layer: nothing persists across server restarts).
"""

from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import AsyncIterator

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse

from trowel_py.cc_host import checkpoint
from trowel_py.cc_host.history import parse_history
from trowel_py.cc_host.models import list_models
from trowel_py.cc_host.service import CCHost
from trowel_py.cc_host.session_scan import count_sessions, list_sessions
from trowel_py.cc_host.slash_items import list_slash_items
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

# slice-028 D2: 多 session 并存状态（模块级，跟 _REGISTRY 同寿命；测试 reset 见
# test_routes._mini_app）。Q5' 资源限制：MAX_RUNNING 活跃 SSE / MAX_CONNECTIONS 总连接。
_WORKDIR_INDEX: dict[str, set[str]] = {}   # workdir → {sid}（命名序号 + 按 workdir 查询）
_SESSION_NAMES: dict[str, str] = {}         # sid → 显示名（basename + #N）
_ACTIVE_SID: str | None = None              # 当前活跃 session（多开切换）
MAX_RUNNING = 5                             # slice-028 Q5': 同时在跑 turn 的 session 上限
MAX_CONNECTIONS = 20                        # slice-028 Q5': 已创建 session 总数上限


def _session_display_name(workdir: str) -> str:
    """slice-028 D2 Q6: workdir basename + 同 workdir 多开加序号。

    第一个无序号（basename），后续 #2 #3 ...。在 sid 注册进 _WORKDIR_INDEX
    *之前* 调用——序号 = 已存在数量 + 1。
    """
    basename = Path(workdir).name or str(workdir)
    existing = len(_WORKDIR_INDEX.get(workdir, ()))
    return basename if existing == 0 else f"{basename} #{existing + 1}"


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


@dataclass(frozen=True)
class OpenedCcSession:
    """Result of opening a CC session (slice-072).

    Shared by POST ``/api/cc/sessions`` and the ``/api/agent`` CC branch so the
    live ``_REGISTRY`` stays the single CC session store (spec C-5 — no
    repo-wide rename, no second CC session pool).

    Attributes:
        sid: the new trowel session id.
        host: the constructed CCHost (already registered).
        name: multi-session display name (workdir basename + ``#N``).
    """

    sid: str
    host: CCHost
    name: str


def open_cc_session(
    req: CreateSessionRequest,
    request: Request,
    registry: dict[str, CCHost] | None = None,
) -> OpenedCcSession:
    """Build a CCHost, register it in the live registry + multi-session state.

    slice-072: extracted verbatim from the old ``create_session`` body so the
    new host-neutral Session Hub can open a CC session through the same path
    without duplicating the construction / multi-open bookkeeping. Behavior is
    unchanged for ``/api/cc/sessions`` (the existing route tests guard that).

    Args:
        req: the create request (workdir / model / effort / permission /
            M/P switches / resume_from).
        request: the FastAPI request (read for ``app.state`` proxy + settings).
        registry: override the module ``_REGISTRY`` (tests inject a fresh
            dict). Defaults to the module-level registry.

    Returns:
        The opened session (sid + host + display name).

    Raises:
        HTTPException: 400 if the workdir does not exist; 409 if the live
            connection cap (``MAX_CONNECTIONS``) is reached.
    """

    if registry is None:
        registry = _REGISTRY
    if not Path(req.workdir).is_dir():
        raise HTTPException(status_code=400, detail="workdir does not exist")
    # 连接数限制（已创建 session 总数）
    if len(registry) >= MAX_CONNECTIONS:
        raise HTTPException(
            status_code=409,
            detail=f"连接数已达上限（{MAX_CONNECTIONS}），请先关闭一些 session",
        )
    sid = uuid.uuid4().hex
    # slice-040-c: attach the memory MCP server (search/read/outcome) so the
    # model can query notes. write_mcp_config is idempotent. Imported from the
    # SDK-free mcp_config module so session creation does not require the mcp
    # package (only the spawned subprocess does).
    from trowel_py.memory.mcp_config import write_mcp_config

    # slice-060 C-3: attach the memory MCP only when the memory switch is on.
    # (CCHost also freezes mcp to None when memory is off, so this is belt-and-
    # braces.) A memory-off session skips write_mcp_config ON PURPOSE: it never
    # consumes that file (CCHost._mcp_config is None), and every memory-on
    # create rewrites it idempotently — so the shared global file stays current
    # regardless of which session was created last.
    mcp_config = str(write_mcp_config()) if req.memory_enabled else None
    host = CCHost(
        sid,
        req.workdir,
        model=req.model,
        effort=req.effort,
        permission_mode=req.permission_mode,
        resume_from=req.resume_from,
        proxy_base_url=getattr(request.app.state, "proxy_base_url", None),
        settings_path=getattr(request.app.state, "cc_settings_path", None),
        mcp_config=mcp_config,
        memory_enabled=req.memory_enabled,
        profile_enabled=req.profile_enabled,
        self_enabled=req.self_enabled,
    )
    registry[sid] = host
    # 注册多开状态（命名序号 + workdir 索引 + 设为活跃）
    name = _session_display_name(req.workdir)
    _WORKDIR_INDEX.setdefault(req.workdir, set()).add(sid)
    _SESSION_NAMES[sid] = name
    global _ACTIVE_SID
    _ACTIVE_SID = sid
    return OpenedCcSession(sid=sid, host=host, name=name)


@router.post("/sessions")
def create_session(
    req: CreateSessionRequest,
    request: Request,
    registry: dict[str, CCHost] = Depends(get_registry),
) -> dict:
    """Open a new session (optionally resuming a CC session id)."""
    opened = open_cc_session(req, request, registry)
    return {
        "success": True,
        "data": {
            "session_id": opened.sid,
            "cc_session_id": req.resume_from,
            "model": opened.host.model,
            "name": opened.name,
            # whether reverting turns is supported for this workdir
            # (non-git workdirs get the banner + no revert buttons in the UI).
            "revert_enabled": checkpoint.is_enabled()
            and checkpoint.is_git_repo(req.workdir),
            # slice-060: surface the frozen A/B condition so the frontend can
            # render it and reconcile on refresh (C-5/C-6).
            "memory_enabled": req.memory_enabled,
            "profile_enabled": req.profile_enabled,
        },
        "error": None,
    }


@router.get("/sessions/active")
def list_active_sessions(
    registry: dict[str, CCHost] = Depends(get_registry),
) -> dict:
    """列出当前 trowel 进程的活跃 session（_REGISTRY）。

    区别于 GET /sessions（磁盘历史）。前端多开栏用这个 + active_id 渲染统一列表。
    返回所有 _REGISTRY 条目（含从未发消息的 temp），每条带 connected 字段：
    True = 有活 cc 子进程（temp 为 False）。前端据此过滤多开栏，避免 temp 被误显示。
    """
    sessions = [
        {
            "id": sid,
            "workdir": host.workdir,
            "model": host.model,
            "name": _SESSION_NAMES.get(sid, Path(host.workdir).name),
            "running": getattr(host, "running", False),
            # True = 有活 cc 子进程（发过消息且未退出）；temp（从未 spawn）→ False。
            # 注：is_dead 也覆盖"曾连接但已退出"的会话 —— 它们同样 connected=False。
            # 前端 exited 状态由 SSE 事件驱动，page refresh 后该标志丢失，这类会话
            # 会成为不显示的幽灵行（已知清理项，非本次 bug 范围）。
            "connected": not getattr(host, "is_dead", True),
            # slice-060: frozen A/B condition per session (defaults True for the
            # duck-typed FakeHost used elsewhere in tests).
            "memory_enabled": getattr(host, "memory_enabled", True),
            "profile_enabled": getattr(host, "profile_enabled", True),
        }
        for sid, host in registry.items()
    ]
    return {
        "success": True,
        "data": {"sessions": sessions, "active_id": _ACTIVE_SID},
        "error": None,
    }


@router.post("/sessions/{sid}/activate")
def activate_session(
    sid: str,
    registry: dict[str, CCHost] = Depends(get_registry),
) -> dict:
    """切换当前活跃 session（多开切换，不销毁非活跃）。"""
    _require(sid, registry)
    global _ACTIVE_SID
    _ACTIVE_SID = sid
    return {"success": True, "data": {"active_id": sid}, "error": None}


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
            "cc_session_id": meta.cc_session_id,
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
    events = parse_history(host.workdir, cc_session_id)
    return {"success": True, "data": [e.model_dump() for e in events], "error": None}


@router.get("/models")
def list_models_endpoint() -> dict:
    """List available model aliases from cc's settings.json env (slice-027 C2).

    Returns the alias -> real-model mapping for the /model picker. trowel does
    not hardcode model ids; it surfaces cc's own aliases (opus/sonnet/haiku)
    which map to the configured backend via ANTHROPIC_DEFAULT_*_MODEL env.
    Switching backends only requires editing settings.json, not trowel code.
    """
    items = [asdict(m) for m in list_models()]
    return {"success": True, "data": items, "error": None}


def _init_roster_for_workdir(
    workdir: str, registry: dict[str, CCHost]
) -> list[str]:
    """slice-042 P1: cc init's slash_commands roster cached on the workdir's
    active cc session — the authoritative name floor that keeps up with cc
    updates (a skill cc adds shows with no trowel code change).

    Prefer the active session, else any session registered for this workdir;
    none found → empty floor (list_slash_items still returns disk+hardcoded).
    """
    sids = _WORKDIR_INDEX.get(workdir, set())
    ordered = ([_ACTIVE_SID] if _ACTIVE_SID in sids else []) + [
        s for s in sids if s != _ACTIVE_SID
    ]
    for sid in ordered:
        roster = getattr(registry.get(sid), "_init_roster", None)
        if roster:
            return roster
    return []


@router.get("/slash-items")
def list_slash_items_endpoint(
    workdir: str = Query(..., min_length=1),
    registry: dict[str, CCHost] = Depends(get_registry),
) -> dict:
    """List slash commands + skills for the '/' autocomplete.

    slice-027 C1: scans <workdir>/.claude/{skills,commands} + ~/.claude/
    {skills,commands} (frontmatter) plus a hardcoded bundled-skill map.
    slice-042 P1: merges the active cc session's init slash_commands roster
    as the name floor (the part that keeps up with cc updates); this endpoint
    adds the descriptions cc init doesn't carry.
    """
    init_roster = _init_roster_for_workdir(workdir, registry)
    items = [asdict(i) for i in list_slash_items(workdir, init_roster=init_roster)]
    return {"success": True, "data": items, "error": None}


@router.get("/list-dir")
def list_dir(
    path: str = Query(..., min_length=1),
) -> dict:
    """List immediate subdirectories of a path (slice-027 workdir tree browser).

    The browser sandbox forbids enumerating local directories, so WorkdirPicker's
    tree fetches children here. Returns subdirectories only (files omitted — a
    workdir must be a directory); dotted names (`.git`, `.venv`, ...) hidden to
    cut noise. `~` is expanded server-side. Non-directory / missing path → 400.
    """
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
    """Close a CC session: kill the subprocess + drop registry + multi-open state.

    slice-072: extracted from ``delete_session`` so the host-neutral Session
    Hub can close a CC session through the same path, keeping the live
    ``_REGISTRY`` + multi-open bookkeeping consistent regardless of which API
    the deletion came through. Behavior is unchanged for ``/api/cc/*`` (the
    existing route test guards that).

    Args:
        session_id: the trowel session id.
        registry: override the module ``_REGISTRY`` (tests inject a dict).

    Returns:
        True if the session existed and was closed, False if it was already
        gone (idempotent — safe for the Hub to call even when the binding
        outlived the live host).
    """

    if registry is None:
        registry = _REGISTRY
    host = registry.get(session_id)
    if host is None:
        return False
    await host.close()
    registry.pop(session_id, None)
    # slice-028 D2: 清理多开状态
    wd = host.workdir
    if session_id in _WORKDIR_INDEX.get(wd, set()):
        _WORKDIR_INDEX[wd].discard(session_id)
        if not _WORKDIR_INDEX[wd]:
            _WORKDIR_INDEX.pop(wd, None)
    _SESSION_NAMES.pop(session_id, None)
    global _ACTIVE_SID
    if _ACTIVE_SID == session_id:
        _ACTIVE_SID = None
    return True


@router.delete("/sessions/{sid}")
async def delete_session(
    sid: str,
    registry: dict[str, CCHost] = Depends(get_registry),
) -> dict:
    """Kill the subprocess and remove the session from the registry."""
    _require(sid, registry)  # keep the 404 behavior for the legacy route
    closed = await close_cc_session(sid, registry)
    return {"success": True, "data": {"closed": closed}, "error": None}
