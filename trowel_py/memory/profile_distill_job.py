"""daily profile-distillation orchestration (slice-050).

The sister task of review_job: every day it reads each not-yet-distilled
session's jsonl and drives a cc agent through the distill prompt, producing
profile suggestions that land in ``meta/profile-suggestions.json`` as pending
candidates. The agent NEVER writes profile.md — that's the user's accept path
(C-1 structural provenance).

Mirrors review_job's shape: flock over the run, per-session cc via an injectable
``host_factory`` (tests never spawn real cc — #46416), and a draft file the
agent writes (``suggestions-draft.json``). Diverges where slice-050 decided:

- cc goes THROUGH the proxy (``proxy_base_url`` passed in — C-4, 529 prep),
  unlike review_job's deliberate None.
- watermark is its own file (``meta/profile-distill-state.json`` — C-7), not
  sessions.db, so the two daily jobs don't trip over each other.
- candidates come from ``find_all_completed_sessions`` (independent of review's
  ``last_extracted_offset``), filtered to ``completed > processed.end_offset``.
"""
from __future__ import annotations

import contextlib
import json
import logging
import os
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

try:  # Unix-only; Windows has no flock → the lock becomes a no-op there.
    import fcntl
except ImportError:  # pragma: no cover — non-Unix
    fcntl = None  # type: ignore[assignment]

from trowel_py.memory.paths import resolve_memory_root
from trowel_py.memory.profile_distill_prompt import build_distill_prompt
from trowel_py.memory.profile_distill_state import load_processed, mark_processed
from trowel_py.memory.profile_suggestions import append_suggestions, load_suggestions
from trowel_py.memory.sessions_repo import (
    SessionRecord,
    create_sessions_repository,
    open_sessions_db,
)
from trowel_py.memory.store import MemoryStore
from trowel_py.memory.types import Suggestion

logger = logging.getLogger(__name__)

#: a callable that builds a cc host for one session in its distill workdir.
#: Tests inject a fake; production leaves it None → a real CCHost is built.
HostFactory = Callable[[SessionRecord, Path], Any]

_VALID_DIMS: frozenset[str] = frozenset(
    {"ability", "methodology", "expression", "goal", "other"}
)
_DISTILL_WORKDIR_NAME = "distill-work"
_DRAFT_FILE = "suggestions-draft.json"


class DistillError(Exception):
    """A session failed to distill-for-profile (agent error / no draft / bad draft).

    run_daily_distill catches this per session and skips (does NOT mark
    processed, so the session is retried next run — C-6).
    """


def _ensure_distill_workdir(date_str: str, memory_root: Path) -> Path:
    """Create the dated distill workdir (sibling of review-daily-work)."""
    workdir = memory_root.parent / _DISTILL_WORKDIR_NAME / date_str
    workdir.mkdir(parents=True, exist_ok=True)
    return workdir


def _stamp_sources(sources: object, cc_session_id: str) -> tuple[str, ...]:
    """Ensure the suggestion carries cc_session_id for traceability (C-2).

    The agent's ``sources`` may be a list of user-quote fragments; the job
    prepends the cc_session_id (if not already present) so every suggestion is
    traceable to its source session even if the agent omitted it.
    """
    if isinstance(sources, list):
        out = [str(s) for s in sources]
    else:
        if sources:
            logger.debug(
                "distill: suggestion sources not a list, dropping: %r", sources
            )
        out = []
    if cc_session_id and cc_session_id not in out:
        out = [cc_session_id, *out]
    return tuple(out)


def _parse_draft(text: str, *, cc_session_id: str, date_str: str) -> list[Suggestion]:
    """Parse the agent's ``suggestions-draft.json`` into Suggestion values.

    The agent emits ``dimension``/``body``/``sources``/``rationale``; the job
    stamps ``id`` (uuid), ``date``, ``status`` (pending). ``rationale`` is
    dropped (the queue stays lean; ``sources`` already make it traceable).

    Raises:
        DistillError: bad JSON, or a suggestion carries an unknown dimension.
    """
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise DistillError(f"suggestions-draft.json is not valid JSON: {exc}") from exc
    raw = data.get("suggestions", []) if isinstance(data, dict) else []
    out: list[Suggestion] = []
    for item in raw:
        if not isinstance(item, dict):
            logger.debug("distill: skipping non-dict suggestion item: %r", item)
            continue
        dim = item.get("dimension")
        if dim not in _VALID_DIMS:
            raise DistillError(
                f"unknown dimension {dim!r} in suggestions-draft.json"
            )
        out.append(
            Suggestion(
                id=uuid.uuid4().hex,
                dimension=dim,  # type: ignore[arg-type]
                body=item.get("body") or "",
                sources=_stamp_sources(item.get("sources", []), cc_session_id),
                date=date_str,
                status="pending",  # type: ignore[arg-type]
            )
        )
    return out


async def run_one_session(
    session: SessionRecord,
    date_str: str,
    memory_root: Path,
    *,
    proxy_base_url: str,
    settings_path: Path | str | None = None,
    host_factory: HostFactory | None = None,
    start_offset: int | None = None,
    end_offset: int | None = None,
) -> list[Suggestion]:
    """Drive the distill agent on one session; return its parsed suggestions.

    Args:
        session: the SessionRecord to distill-for-profile.
        date_str: target day (labels the workdir + the suggestions' date).
        memory_root: memory root.
        proxy_base_url: the trowel proxy URL (C-4). Passed to the real CCHost;
            a host_factory fake ignores it.
        settings_path: path to ~/.claude/settings.json. REQUIRED when
            proxy_base_url is set — the proxy turns on
            CLAUDE_CODE_PROVIDER_MANAGED_BY_HOST, which makes cc strip provider
            vars from settings.json; load_settings_env(settings_path) must
            re-inject them into the spawn env, else the cc subprocess 401s
            (slice-050 code-review CR [1]). A host_factory fake ignores it.
        host_factory: optional ``(session, workdir) -> cc host``. None → a real
            CCHost built in the session's distill workdir.
        start_offset / end_offset: incremental byte range (a resumed session's
            new turns); None/None distils the whole session.

    Raises:
        DistillError: the agent did not finish cleanly, produced no
            suggestions-draft.json, or the draft was malformed.
    """
    store = MemoryStore(memory_root)
    # a corrupt suggestion queue must NOT crash the distill run (would be a
    # sticky failure since the bad file stays). Degrade to empty — worst case
    # the agent re-proposes something the queue already had (soft dedup).
    try:
        existing = load_suggestions(memory_root)
    except ValueError:
        logger.warning(
            "distill: corrupt suggestion queue; deduping against empty"
        )
        existing = []
    prompt = build_distill_prompt(
        session.jsonl_path or "",
        existing,
        store.load_profile(),
        start_offset=start_offset,
        end_offset=end_offset,
    )

    base_workdir = _ensure_distill_workdir(date_str, memory_root)
    workdir = base_workdir / session.cc_session_id
    workdir.mkdir(parents=True, exist_ok=True)

    if host_factory is not None:
        host = host_factory(session, workdir)
    else:
        from trowel_py.cc_host.service import CCHost
        from trowel_py.memory.mcp_config import write_mcp_config

        # proxy_base_url: C-4 — go through the trowel proxy (529 prep for the
        # future per-session cadence). settings_path: REQUIRED with the proxy —
        # load_settings_env re-injects provider vars the proxy strips (CR [1]).
        # session_kind="distill": keep this agent's own session out of its
        # candidate queue (C-5: kind, not workdir).
        host = CCHost(
            session_id=uuid.uuid4().hex,
            workdir=str(workdir),
            session_kind="distill",
            proxy_base_url=proxy_base_url,
            settings_path=settings_path,
            mcp_config=str(write_mcp_config()),
        )

    finished = False
    try:
        async for event in host.send(prompt):
            # duck-typed: a real FinishedEvent carries type=="finished".
            if getattr(event, "type", None) == "finished":
                finished = True
    finally:
        close = getattr(host, "close", None)
        if close is not None:
            await close()

    if not finished:
        raise DistillError(
            f"distill agent did not finish cleanly for {session.cc_session_id}"
        )

    draft_path = workdir / _DRAFT_FILE
    if not draft_path.exists():
        raise DistillError(
            f"distill agent produced no {_DRAFT_FILE} for {session.cc_session_id}"
        )
    return _parse_draft(
        draft_path.read_text(encoding="utf-8"),
        cc_session_id=session.cc_session_id,
        date_str=date_str,
    )


@contextlib.contextmanager
def _distill_lock(root: Path):
    """Mutex so concurrent distill runs skip (mirrors review_job._review_lock).

    Off-Unix (``fcntl`` is None) the lock is a no-op — mutual exclusion then
    relies on the caller being single-instance in practice.
    """
    if fcntl is None:
        yield
        return
    lock_path = root / "meta" / ".distill.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        os.close(fd)
        raise
    try:
        yield
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


async def run_daily_distill(
    memory_root: Path | None,
    proxy_base_url: str,
    *,
    settings_path: Path | str | None = None,
    host_factory: HostFactory | None = None,
    date_str: str | None = None,
) -> None:
    """Daily batch: distill every session with new content not yet profile-distilled.

    Holds an exclusive flock so two distill runs never overlap. Candidates come
    from ``find_all_completed_sessions`` (independent of review's watermark —
    C-7); a candidate is in the backlog iff its completed offset is past its
    distill watermark (``processed.end_offset``). Each is distilled, new
    suggestions appended to the queue, then marked processed. A failed session
    is skipped WITHOUT marking (retried next run — C-6).

    ``memory_root=None`` resolves the standard memory root (mirrors
    ``run_daily_review``), so the sync wrapper / CLI can omit it.
    """
    root = memory_root if memory_root is not None else resolve_memory_root()
    if date_str is None:
        date_str = datetime.now().date().isoformat()
    try:
        with _distill_lock(root):
            await _run_daily_distill_locked(
                root, proxy_base_url, settings_path, host_factory, date_str
            )
    except BlockingIOError:
        logger.warning("profile distill already running; skipping this run")


async def _run_daily_distill_locked(
    root: Path,
    proxy_base_url: str,
    settings_path: Path | str | None,
    host_factory: HostFactory | None,
    date_str: str,
) -> None:
    """The locked body: find candidates → distill each → append → mark processed."""
    conn = open_sessions_db(root)
    try:
        repo = create_sessions_repository(conn)
        candidates = repo.find_all_completed_sessions()
        processed = load_processed(root)
        backlog: list[tuple[SessionRecord, int, int]] = []
        for session in candidates:
            end = session.last_completed_offset or 0
            start = processed[session.cc_session_id].end_offset if session.cc_session_id in processed else 0
            if end > start:
                backlog.append((session, start, end))
        logger.info(
            "profile distill: %d candidate(s), %d with new content (date_str=%s)",
            len(candidates),
            len(backlog),
            date_str,
        )
        for session, start, end in backlog:
            try:
                suggestions = await run_one_session(
                    session,
                    date_str,
                    root,
                    proxy_base_url=proxy_base_url,
                    settings_path=settings_path,
                    host_factory=host_factory,
                    start_offset=start or None,
                    end_offset=end,
                )
            except DistillError as exc:
                logger.warning(
                    "profile distill failed for %s (skipped, not marked): %s",
                    session.cc_session_id,
                    exc,
                )
                continue
            if suggestions:
                append_suggestions(root, suggestions, updated=date_str)
                logger.info(
                    "profile distill: +%d suggestion(s) from %s",
                    len(suggestions),
                    session.cc_session_id,
                )
            mark_processed(
                root,
                session.cc_session_id,
                end_offset=end,
                at=datetime.now().isoformat(),
            )
    finally:
        conn.close()


def run_daily_distill_sync(event: Any = None) -> None:
    """Sync wrapper for the scheduler / future timer entry (mirrors
    review_job.run_daily_review_sync).

    Wraps the async ``run_daily_distill`` in ``asyncio.run``. Reads ``root`` /
    ``date`` / ``proxy_base_url`` from the event dict (set by the scheduler's
    ``_run_once``).
    """
    import asyncio

    root = None
    date_str = None
    proxy_base_url = ""
    settings_path = None
    if event and isinstance(event, dict):
        root = event.get("root")
        date_str = event.get("date")
        proxy_base_url = event.get("proxy_base_url", "")
        settings_path = event.get("settings_path")
    root_path = Path(root) if root else None
    asyncio.run(
        run_daily_distill(
            root_path,
            proxy_base_url,
            settings_path=settings_path,
            date_str=date_str,
        )
    )
