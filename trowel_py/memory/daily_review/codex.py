"""Codex completed turn 到共享 memory persist 的编排。"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path

from trowel_py.memory.activity_dates import extract_activity_dates
from trowel_py.memory.daily_review.agent import (
    DistillError,
    HostFactory,
    run_one_session,
)
from trowel_py.memory.draft import procedure_warnings
from trowel_py.memory.dualtrack import audit_draft
from trowel_py.memory.judge import judge_session
from trowel_py.memory.persist import persist_draft
from trowel_py.memory.provenance import (
    CodexTurnsSource,
    CompletedSegment,
    DerivationProvenance,
    ModelIdentity,
)
from trowel_py.memory.sessions_repo import (
    CodexTurnRecord,
    SessionRecord,
    SessionsRepository,
)
from trowel_py.memory.store import MemoryStore
from trowel_py.memory.types import PersistContext

logger = logging.getLogger("trowel_py.memory.review_job")


async def review_codex_segments(
    root: Path,
    date_str: str,
    repo: SessionsRepository,
    store: MemoryStore,
    *,
    host_factory: HostFactory | None,
    completed_before: str | None,
) -> set[str]:
    segments = repo.find_incremental_codex(completed_before=completed_before)
    logger.info(
        "daily review: %d Codex completed turn(s) (date_str=%s)",
        len(segments),
        date_str,
    )
    touched_dates: set[str] = set()
    for segment in segments:
        turn = segment.turn
        journal_path = Path(turn.journal_path)
        if not journal_path.is_file():
            logger.warning(
                "Codex journal missing for %s:%s (not advanced)",
                turn.thread_id,
                turn.turn_id,
            )
            continue
        session = SessionRecord(
            cc_session_id=turn.thread_id,
            trowel_session_id=turn.trowel_session_id,
            workdir=turn.workdir,
            date=(turn.completed_at or date_str)[:10],
            jsonl_path=str(journal_path),
            registered_at=turn.registered_at,
            last_completed_at=turn.completed_at,
        )
        derivation: DerivationProvenance | None = None

        def capture_derivation(value: DerivationProvenance) -> None:
            nonlocal derivation
            derivation = value

        try:
            draft = await run_one_session(
                session,
                date_str,
                root,
                host_factory=host_factory,
                derivation_sink=capture_derivation,
                source_runtime="codex",
            )
        except DistillError as exc:
            logger.warning(
                "Codex distill failed for %s:%s (not advanced): %s",
                turn.thread_id,
                turn.turn_id,
                exc,
            )
            continue

        activity = extract_activity_dates(
            journal_path,
            0,
            0,
            last_completed_at=turn.completed_at,
            registered_at=turn.registered_at,
        )
        bad_dates = _out_of_range_dates(draft.diary, activity.dates)
        if bad_dates:
            logger.warning(
                "Codex draft diary dates %s outside activity_dates %s for %s:%s "
                "(not advanced)",
                bad_dates,
                activity.dates,
                turn.thread_id,
                turn.turn_id,
            )
            continue

        audit = audit_draft(draft)
        if not audit.clean:
            logger.warning(
                "dualtrack leaks in Codex %s:%s: %s",
                turn.thread_id,
                turn.turn_id,
                [(leak.date, leak.signal, leak.snippet) for leak in audit.leaks],
            )
        warnings = procedure_warnings(draft)
        if warnings:
            logger.warning(
                "procedure gaps in Codex %s:%s: %s",
                turn.thread_id,
                turn.turn_id,
                warnings,
            )

        context = _context_for_codex(
            turn,
            date_str,
            activity_dates=activity.dates,
            date_basis=activity.basis,
            derivation=derivation,
        )
        try:
            report = persist_draft(store, draft, context)
        except (OSError, ValueError) as exc:
            logger.warning(
                "Codex persist failed for %s:%s (not advanced): %s",
                turn.thread_id,
                turn.turn_id,
                exc,
            )
            continue
        if not report.ok:
            logger.warning(
                "Codex persist incomplete for %s:%s (not advanced)",
                turn.thread_id,
                turn.turn_id,
            )
            continue

        repo.advance_codex_extracted(turn.thread_id, turn.turn_id)
        touched_dates.update(entry.date for entry in draft.diary)
        try:
            await judge_session(
                session,
                date_str,
                root,
                host_factory=host_factory,
                segment_id=context.segment_id,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Codex judge raised for %s:%s (isolated): %s",
                turn.thread_id,
                turn.turn_id,
                exc,
            )
    return touched_dates


def _context_for_codex(
    turn: CodexTurnRecord,
    review_date: str,
    *,
    activity_dates: tuple[str, ...],
    date_basis: str,
    derivation: DerivationProvenance | None,
) -> PersistContext:
    segment_id = f"codex:{turn.thread_id}:{turn.turn_id}"
    source_models: tuple[ModelIdentity, ...] = ()
    if any((turn.model, turn.effort, turn.provider)):
        source_models = (
            ModelIdentity(
                model=turn.model,
                effort=turn.effort,
                provider=turn.provider,
                basis="binding",
            ),
        )
    completed_segment = CompletedSegment(
        segment_id=segment_id,
        host_kind="codex",
        native_session_id=turn.thread_id,
        trowel_session_ids=(turn.trowel_session_id,),
        session_kind=turn.session_kind,
        workdir=turn.workdir,
        registered_at=turn.registered_at,
        completed_at=turn.completed_at or "",
        source=CodexTurnsSource(turn_ids=(turn.turn_id,)),
        source_models=source_models,
    )
    return PersistContext(
        segment_id=segment_id,
        cc_session_id=turn.thread_id,
        workdir=turn.workdir,
        registered_at=turn.registered_at,
        review_date=review_date,
        source_jsonl=turn.journal_path,
        activity_dates=activity_dates,
        date_basis=date_basis,
        processed_date=datetime.now().date().isoformat(),
        completed_segment=completed_segment,
        derivation=derivation,
    )


def _out_of_range_dates(
    diary: tuple,
    activity_dates: tuple[str, ...],
) -> tuple[str, ...]:
    if not activity_dates:
        return tuple(entry.date for entry in diary)
    allowed = set(activity_dates)
    return tuple(entry.date for entry in diary if entry.date not in allowed)
