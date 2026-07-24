"""North-star 测试数据。"""

from __future__ import annotations

from pathlib import Path

from trowel_py.memory.access_log import AccessRecord, log_access
from trowel_py.memory.judgements import (
    HitJudgement,
    JudgementReport,
    MissJudgement,
)
from trowel_py.memory.sessions_repo import (
    SessionRecord,
    create_sessions_repository,
    open_sessions_db,
)
from trowel_py.memory.store import MemoryStore


def write_note(
    root: Path,
    memory_id: str,
    *,
    status: str = "active",
    harmful_refs: int = 0,
) -> str:
    return MemoryStore(root).write_note(
        {
            "type": "note",
            "title": f"n-{memory_id}",
            "verification": "verified",
            "memory_id": memory_id,
            "status": status,
            "harmful_refs": harmful_refs,
            "__body": "x",
        }
    )


def log_records(
    root: Path,
    cc_session_id: str,
    action: str,
    *,
    count: int = 1,
    query: str = "q",
) -> None:
    for index in range(count):
        log_access(
            root,
            AccessRecord(
                ts="2026-07-16T10:00:00+00:00",
                trowel_session_id="t",
                cc_session_id=cc_session_id,
                toolUseId=f"tu-{cc_session_id}-{action}-{index}",
                action=action,  # type: ignore[arg-type]
                search_id="s" if action == "search" else "",
                read_id="r" if action == "read" else "",
                query=query if action == "search" else "",
                memory_id=f"m-{index}" if action in ("search", "read") else "",
                rank=index if action == "search" else None,
            ),
        )


def seed_session(
    root: Path,
    cc_session_id: str,
    kind: str = "user",
) -> None:
    conn = open_sessions_db(root)
    try:
        create_sessions_repository(conn).register(
            SessionRecord(
                cc_session_id=cc_session_id,
                workdir="/project",
                date="2026-07-16",
                registered_at="2026-07-16T10:00:00",
                session_kind=kind,
            )
        )
    finally:
        conn.close()


def hit(memory_id: str, outcome: str, used: bool = True) -> HitJudgement:
    return HitJudgement(
        memory_id=memory_id,
        used=used,
        outcome=outcome,  # type: ignore[arg-type]
        reason="r",
        evidence="e",
    )


def miss(memory_id: str, attribution: str) -> MissJudgement:
    return MissJudgement(
        memory_id=memory_id,
        attribution=attribution,  # type: ignore[arg-type]
        reason="r",
        evidence="e",
    )


def report(
    cc_session_id: str,
    hits=(),
    recall_miss=(),
    summary: str = "s",
    segment: str = "",
) -> JudgementReport:
    return JudgementReport(
        cc_session_id=cc_session_id,
        hits=tuple(hits),
        recall_miss=tuple(recall_miss),
        summary=summary,
        segment_id=segment,
    )
