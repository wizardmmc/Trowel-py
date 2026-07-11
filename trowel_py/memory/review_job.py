"""daily-review distillation orchestration (slice-040 T11, evolved in 040-b).

The only async module in slice-040. ``run_one_session`` drives one cc agent (a
``CCHost`` constructed directly, NOT over HTTP) through the refine prompt, then
reads its ``draft.json``. ``run_daily_review`` holds an flock (C-3) and batches
every incremental slice: find_incremental → distil each → persist → audit →
advance_extracted (slice-040-b: no longer find_pending + mark_extracted).

The cc host is injectable (``host_factory``) so tests never spawn a real cc
(#46416 — never nest ``claude -p`` inside an interactive session; benchmarks
that DO need a real agent run out-of-band, not in CI).
"""
from __future__ import annotations

import contextlib
import json
import logging
import os
import uuid
from datetime import date, datetime
from pathlib import Path
from typing import Any, Callable

try:  # Unix-only; Windows has no flock → the lock becomes a no-op there.
    import fcntl
except ImportError:  # pragma: no cover — non-Unix
    fcntl = None  # type: ignore[assignment]

from trowel_py.memory.cost import SessionCost, extract_cost_from_jsonl
from trowel_py.memory.draft import Draft, parse_draft, procedure_warnings, validate_draft
from trowel_py.memory.dualtrack import audit_draft
from trowel_py.memory.paths import resolve_memory_root
from trowel_py.memory.persist import persist_draft
from trowel_py.memory.prompt import build_refine_prompt
from trowel_py.memory.review_workspace import ensure_review_workdir
from trowel_py.memory.sessions_repo import (
    SessionRecord,
    create_sessions_repository,
    open_sessions_db,
)
from trowel_py.memory.store import MemoryStore
from trowel_py.memory.types import PersistContext

logger = logging.getLogger(__name__)

#: a callable that builds a cc host for one session in its review workdir.
#: Tests inject a fake; production leaves it None → a real CCHost is built.
HostFactory = Callable[[SessionRecord, Path], Any]


class DistillError(Exception):
    """A session failed to distill (agent error / no draft / invalid draft).

    run_daily_review catches this per session and skips (does NOT mark
    extracted, so the session stays pending and can be retried).
    """


def _cost_text(cost: SessionCost) -> str:
    return f"tokens={cost.total_tokens} turns={cost.num_turns} errors={cost.error_count}"


async def run_one_session(
    session: SessionRecord,
    date_str: str,
    memory_root: Path,
    *,
    host_factory: HostFactory | None = None,
    start_offset: int | None = None,
    end_offset: int | None = None,
) -> Draft:
    """Drive the refine agent on one session; return its parsed, validated draft.

    Args:
        session: the pending SessionRecord to distill.
        date_str: target day (names the review workdir).
        memory_root: memory root (for the review workdir location).
        host_factory: optional callable ``(session, workdir) -> cc host``. When
            None, a real ``CCHost`` is built in the session's review workdir
            (auto-injected with memory — step 1 "查已有" for free).
        start_offset / end_offset: slice-040-b incremental byte range. The agent
            reads the full session for context but the prompt tells it to only
            produce NEW memory for ``[start_offset, end_offset]`` (earlier turns
            were distilled in a prior run). None/None distils the whole session.

    Raises:
        DistillError: the agent did not finish cleanly, produced no draft.json,
            or the draft failed validation / was malformed.
    """
    cost = extract_cost_from_jsonl(session.jsonl_path)
    prompt = build_refine_prompt(
        session.jsonl_path,
        _cost_text(cost),
        start_offset=start_offset,
        end_offset=end_offset,
    )

    base_workdir = ensure_review_workdir(date_str, memory_root)
    workdir = base_workdir / session.cc_session_id
    workdir.mkdir(parents=True, exist_ok=True)

    if host_factory is not None:
        host = host_factory(session, workdir)
    else:
        from trowel_py.cc_host.service import CCHost

        # proxy_base_url deliberately None: the daily review runs as a CLI /
        # timer (not under the FastAPI lifespan), so there is no trowel proxy.
        # cc reads ~/.claude/settings.json directly to reach the GLM endpoint —
        # the proxy was only for identity-rewrite + cache, which distillation
        # does not need. memory injection is added by CCHost._spawn via
        # --append-system-prompt, independent of the proxy. Verify end-to-end
        # in a standalone terminal (#46416 — never nest in interactive claude).
        from trowel_py.memory.mcp_server import write_mcp_config

        host = CCHost(
            session_id=uuid.uuid4().hex,
            workdir=str(workdir),
            # slice-040-b: stamp the distillation session so its own init does
            # not re-enter the daily-review queue (C-5: kind, not workdir guess).
            session_kind="review",
            # slice-040-c: attach memory MCP so the refine agent can search
            # existing notes (dedupe/lookup). The injection advertises
            # memory.search — must back it with the actual tool (codex P2-1).
            mcp_config=str(write_mcp_config()),
        )

    finished = False
    try:
        async for event in host.send(prompt):
            # duck-typed: a real FinishedEvent carries type=="finished"; an
            # ErrorEvent carries type=="error" (finished stays False).
            if getattr(event, "type", None) == "finished":
                finished = True
    finally:
        close = getattr(host, "close", None)
        if close is not None:
            await close()

    if not finished:
        raise DistillError(
            f"agent did not finish cleanly for {session.cc_session_id}"
        )

    draft_path = workdir / "draft.json"
    if not draft_path.exists():
        raise DistillError(
            f"agent produced no draft.json for {session.cc_session_id}"
        )
    try:
        draft = parse_draft(draft_path.read_text(encoding="utf-8"))
        errors = validate_draft(draft)
        if errors:
            raise DistillError(f"invalid draft for {session.cc_session_id}: {errors}")
    except DistillError:
        raise
    except (json.JSONDecodeError, ValueError, TypeError) as exc:
        # malformed draft.json (bad JSON, non-int pain, …) → skip this session,
        # not crash the whole daily review.
        raise DistillError(f"invalid draft for {session.cc_session_id}: {exc}") from exc
    return draft


@contextlib.contextmanager
def _review_lock(root: Path):
    """C-3 mutex: exclusive flock so concurrent review jobs skip (slice-040-b).

    The timer and a manual ``trowel memory review`` could race; the second
    caller takes ``BlockingIOError`` out of this contextmanager, the wrapper
    logs + skips. Off-Unix (``fcntl`` is None) the lock is a no-op — mutual
    exclusion then relies on the caller being single-instance in practice.
    """
    if fcntl is None:
        yield
        return
    lock_path = root / "meta" / ".review.lock"
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


async def run_daily_review(
    event: Any = None,
    memory_root: Path | None = None,
    date_str: str | None = None,
    *,
    host_factory: HostFactory | None = None,
) -> None:
    """Daily batch: distill every completed-not-yet-extracted slice (slice-040-b).

    Holds an exclusive flock (C-3) so two reviews never run at once; a concurrent
    caller logs + skips. C-4: the body processes every incremental segment
    (``find_incremental``), not a fixed "yesterday", so missed / sleep-shifted
    runs catch up.

    Args:
        event: the ``dispatch_write_job`` event dict ``{"date": ..., "root": ...}``,
            or None. ``date_str`` / ``memory_root`` overrides take precedence.
        memory_root: memory root override.
        date_str: kept for CLI/event compat; only used as a fallback review_date
            label when a segment's session.date is missing.
        host_factory: optional ``(session, workdir) -> cc host`` for tests.
    """
    root = Path(memory_root) if memory_root is not None else resolve_memory_root()
    if date_str is None:
        if event and isinstance(event, dict) and event.get("date"):
            date_str = str(event["date"])
        else:
            date_str = date.today().isoformat()
    try:
        with _review_lock(root):
            await _run_daily_review_locked(root, date_str, host_factory)
    except BlockingIOError:
        logger.warning("daily review already running; skipping this run")


async def _run_daily_review_locked(
    root: Path, date_str: str, host_factory: HostFactory | None
) -> None:
    """The locked body: find_incremental → distil each slice → advance_extracted."""
    # sqlite calls below are synchronous. The incremental segment count is small
    # (a few to a dozen sessions) and each query is sub-ms, so they run directly
    # in the event loop rather than via run_in_executor (the register埋点 in
    # service.py uses the executor because it fires on EVERY cc session init).
    conn = open_sessions_db(root)
    try:
        repo = create_sessions_repository(conn)
        # slice-040-b C-4: no longer fixed to "yesterday" — process every
        # completed-but-not-yet-extracted slice so a missed / sleep-shifted run
        # catches up. date_str now only labels the review workdir / derived daily
        # when a segment's session.date is missing.
        segments = repo.find_incremental()
        logger.info(
            "daily review: %d incremental segment(s) (date_str=%s)",
            len(segments),
            date_str,
        )
        store = MemoryStore(root)
        touched_dates: set[str] = set()
        created_note_ids: list[str] = []  # slice-040-c: feed dictionary sync
        for seg in segments:
            session = seg.session
            # 040-a behavior: review_date = date_str (the CLI/run date), NOT
            # session.date — a cross-day session lands in the day the user asked
            # to review, not a stray daily for its start date.
            review_date = date_str
            touched_dates.add(review_date)
            try:
                draft = await run_one_session(
                    session,
                    review_date,
                    root,
                    host_factory=host_factory,
                    start_offset=seg.start,
                    end_offset=seg.end,
                )
            except DistillError as exc:
                logger.warning(
                    "distill failed for %s (skipped, not advanced): %s",
                    session.cc_session_id,
                    exc,
                )
                continue
            audit = audit_draft(draft)
            if not audit.clean:
                logger.warning(
                    "dualtrack leaks in %s: %s",
                    session.cc_session_id,
                    [(leak.date, leak.signal, leak.snippet) for leak in audit.leaks],
                )
            proc_warns = procedure_warnings(draft)
            if proc_warns:
                # C-3 soft gate (D5): warn, never reject. A kind=procedure note
                # missing trigger/procedure/stop/anti-pattern still lands — the
                # warning is a TODO nudge for the next distillation pass.
                logger.warning(
                    "procedure gaps in %s: %s",
                    session.cc_session_id,
                    proc_warns,
                )
            context = _context_for(session, review_date, seg.start, seg.end)
            try:
                report = persist_draft(store, draft, context)
            except OSError as exc:
                # C-7: a mid-landing failure leaves no manifest → the segment's
                # extracted water mark is NOT advanced (it stays incremental) and
                # is retried; the manifest + idempotence keep the re-run from
                # duplicating anything that did land.
                logger.warning(
                    "persist failed for %s (skipped, not advanced): %s",
                    session.cc_session_id,
                    exc,
                )
                continue
            if not report.ok:
                logger.warning(
                    "persist incomplete for %s (not advanced)", session.cc_session_id
                )
                continue
            repo.advance_extracted(
                session.cc_session_id, seg.end, datetime.now().isoformat()
            )
            created_note_ids.extend(report.notes_created)
        # Derive the daily aggregate for every touched date (P1 fix: the daily
        # is the union of all episodes for a date, not the last session's
        # overwrite). No-op when nothing landed for a date.
        for review_date in sorted(touched_dates):
            store.derive_daily_from_episodes(review_date)
        # slice-040-c C-3: sync the dictionary with this run's new notes
        # (incremental; falls back to full rebuild if no dictionary yet).
        # Non-fatal — a failure leaves the old dictionary; search degrades to
        # a hint only if the dictionary was empty.
        if created_note_ids:
            try:
                from trowel_py.config import load_llm_config
                from trowel_py.llm.client import AnthropicProvider
                from trowel_py.memory.dictionary import sync_dictionary_incremental

                sync_dictionary_incremental(
                    root, created_note_ids, AnthropicProvider(load_llm_config())
                )
            except Exception:
                logger.warning(
                    "dictionary sync failed (non-fatal; old index kept)", exc_info=True
                )
    finally:
        conn.close()


def _context_for(
    session: SessionRecord, date_str: str, start: int, end: int
) -> PersistContext:
    """Build the PersistContext for one incremental slice (slice-040-b).

    ``segment_id = <cc_session_id>:<start>:<end>`` is the stable manifest key;
    a resumed session's later slice gets a different segment_id (e.g. ``s:2048:
    4096`` after ``s:0:2048``) so the two coexist in the same episode file via
    ``write_episode``'s per-segment upsert.
    """
    return PersistContext(
        segment_id=f"{session.cc_session_id}:{start}:{end}",
        cc_session_id=session.cc_session_id,
        workdir=session.workdir,
        registered_at=session.registered_at,
        review_date=date_str,
        source_jsonl=session.jsonl_path,
        source_start_offset=start,
        source_end_offset=end,
    )


def run_daily_review_sync(event: Any = None) -> None:
    """Sync wrapper for the write_job hook (CLI / future timer entry).

    The hooks registry dispatches sync callables; this wraps the async
    ``run_daily_review`` in ``asyncio.run``. Reads ``root`` / ``date`` from the
    event dict (set by ``_run_memory_review``).
    """
    import asyncio

    root = None
    date_str = None
    if event and isinstance(event, dict):
        root = event.get("root")
        date_str = event.get("date")
    root_path = Path(root) if root else None
    asyncio.run(run_daily_review(event, memory_root=root_path, date_str=date_str))
