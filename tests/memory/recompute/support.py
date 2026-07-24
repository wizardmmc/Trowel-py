"""recompute 效果测试数据。"""

from __future__ import annotations

from datetime import timezone
from pathlib import Path

from trowel_py.memory.access_log import (
    AccessRecord,
    OutcomeRecord,
    log_access,
    log_outcome,
)
from trowel_py.memory.judgements import (
    HitJudgement,
    JudgementReport,
    save_judgement_report,
)
from trowel_py.memory.recompute import compute_note_effects
from trowel_py.memory.sessions_repo import (
    SessionRecord,
    create_sessions_repository,
    open_sessions_db,
)
from trowel_py.memory.store import MemoryStore

TZ = timezone.utc


def _note(root: Path, stem: str, *, memory_id: str | None = None) -> None:
    MemoryStore(root).write_note(
        {
            "type": "note",
            "title": stem,
            "verification": "verified",
            "memory_id": memory_id or stem,
            "__body": "x",
        }
    )


def _seed_kind(
    root: Path, cc: str, kind: str = "user", *, trowel: str | None = None
) -> None:
    conn = open_sessions_db(root)
    try:
        create_sessions_repository(conn).register(
            SessionRecord(
                cc_session_id=cc,
                workdir="/p",
                date="2026-07-01",
                registered_at="t",
                session_kind=kind,
                trowel_session_id=trowel or (f"t-{cc}"),
            )
        )
    finally:
        conn.close()


def _read(
    root: Path,
    cc: str,
    stem: str,
    *,
    read_id: str = "r1",
    ts: str = "2026-07-01T10:00:00+00:00",
    trowel: str | None = None,
) -> None:
    log_access(
        root,
        AccessRecord(
            ts=ts,
            trowel_session_id=trowel or (f"t-{cc}"),
            cc_session_id=cc,
            toolUseId="tu",
            action="read",
            search_id="s",
            read_id=read_id,
            memory_id=stem,
        ),
    )


def _outcome(
    root: Path,
    cc: str,
    stem: str,
    outcome: str,
    *,
    read_id: str = "r1",
    ts: str = "2026-07-01T10:01:00+00:00",
    trowel: str | None = None,
) -> None:
    log_outcome(
        root,
        OutcomeRecord(
            ts=ts,
            trowel_session_id=trowel or (f"t-{cc}"),
            cc_session_id=cc,
            toolUseId="tu",
            read_id=read_id,
            memory_id=stem,
            outcome=outcome,  # type: ignore[arg-type]
        ),
    )


def _hit(mid: str, outcome: str, *, used: bool = True) -> HitJudgement:
    return HitJudgement(
        memory_id=mid,
        used=used,
        outcome=outcome,  # type: ignore[arg-type]
        reason="r",
        evidence="e",
    )


def _judge(
    root: Path, cc: str, hits: tuple[HitJudgement, ...], *, segment: str = ""
) -> None:
    save_judgement_report(
        root,
        JudgementReport(
            cc_session_id=cc,
            hits=hits,
            recall_miss=(),
            summary="s",
            segment_id=segment,
        ),
    )


def _fx(root: Path) -> dict:
    return compute_note_effects(root, local_tz=TZ)
