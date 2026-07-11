"""daily-review distillation orchestration (slice-040 T11).

The only async module in slice-040. ``run_one_session`` drives one cc agent (a
``CCHost`` constructed directly, NOT over HTTP) through the refine prompt, then
reads its ``draft.json``. ``run_daily_review`` batches over a day's pending
sessions: find_pending → distill each → persist → audit → mark_extracted.

The cc host is injectable (``host_factory``) so tests never spawn a real cc
(#46416 — never nest ``claude -p`` inside an interactive session; benchmarks
that DO need a real agent run out-of-band, not in CI).
"""
from __future__ import annotations

import json
import logging
import uuid
from datetime import date, datetime
from pathlib import Path
from typing import Any, Callable

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
) -> Draft:
    """Drive the refine agent on one session; return its parsed, validated draft.

    Args:
        session: the pending SessionRecord to distill.
        date_str: target day (names the review workdir).
        memory_root: memory root (for the review workdir location).
        host_factory: optional callable ``(session, workdir) -> cc host``. When
            None, a real ``CCHost`` is built in the session's review workdir
            (auto-injected with memory — step 1 "查已有" for free).

    Raises:
        DistillError: the agent did not finish cleanly, produced no draft.json,
            or the draft failed validation / was malformed.
    """
    cost = extract_cost_from_jsonl(session.jsonl_path)
    prompt = build_refine_prompt(session.jsonl_path, _cost_text(cost))

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
        host = CCHost(session_id=uuid.uuid4().hex, workdir=str(workdir))

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


async def run_daily_review(
    event: Any = None,
    memory_root: Path | None = None,
    date_str: str | None = None,
    *,
    host_factory: HostFactory | None = None,
) -> None:
    """Daily batch: distill a day's pending sessions, persist, mark extracted.

    Args:
        event: the ``dispatch_write_job`` event dict ``{"date": ..., "root": ...}``,
            or None. ``date_str`` / ``memory_root`` overrides take precedence.
        memory_root: memory root override.
        date_str: target day override (ISO); defaults to ``event["date"]`` or today.
        host_factory: optional ``(session, workdir) -> cc host`` for tests.
    """
    root = Path(memory_root) if memory_root is not None else resolve_memory_root()
    if date_str is None:
        if event and isinstance(event, dict) and event.get("date"):
            date_str = str(event["date"])
        else:
            date_str = date.today().isoformat()

    # sqlite calls below are synchronous. Pending count per day is small (a few
    # to a dozen sessions) and each query is sub-ms, so they run directly in the
    # event loop rather than via run_in_executor (the register埋点 in service.py
    # uses the executor because it fires on EVERY cc session init).
    conn = open_sessions_db(root)
    try:
        repo = create_sessions_repository(conn)
        pending = repo.find_pending(date_str, exclude_workdir_substr="review-daily-work")
        logger.info(
            "daily review: %d pending session(s) for %s", len(pending), date_str
        )
        store = MemoryStore(root)
        for session in pending:
            try:
                draft = await run_one_session(
                    session, date_str, root, host_factory=host_factory
                )
            except DistillError as exc:
                logger.warning(
                    "distill failed for %s (skipped, not marked): %s",
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
            context = _context_for(session, date_str)
            try:
                report = persist_draft(store, draft, context)
            except OSError as exc:
                # C-7: a mid-landing failure leaves no manifest → session stays
                # pending (not marked) and is retried; the manifest + idempotence
                # keep the re-run from duplicating anything that did land.
                logger.warning(
                    "persist failed for %s (skipped, not marked): %s",
                    session.cc_session_id,
                    exc,
                )
                continue
            if not report.ok:
                logger.warning(
                    "persist incomplete for %s (not marked)", session.cc_session_id
                )
                continue
            repo.mark_extracted(session.cc_session_id, datetime.now().isoformat())
        # Derive the daily aggregate ONCE after every session landed (P1 fix:
        # the daily is the union of all episodes for this review_date, not the
        # last session's overwrite). No-op when nothing landed.
        store.derive_daily_from_episodes(date_str)
    finally:
        conn.close()


def _context_for(session: SessionRecord, date_str: str) -> PersistContext:
    """Build the PersistContext for one session (040-a uses a whole-file segment).

    040-b will pass real byte offsets; 040-a segments the whole session as
    ``<cc_session_id>:0:end``.
    """
    return PersistContext(
        segment_id=f"{session.cc_session_id}:0:end",
        cc_session_id=session.cc_session_id,
        workdir=session.workdir,
        registered_at=session.registered_at,
        review_date=date_str,
        source_jsonl=session.jsonl_path,
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
