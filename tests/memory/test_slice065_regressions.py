from __future__ import annotations

from datetime import timezone
from pathlib import Path

import pytest

from trowel_py.memory.access_log import (
    AccessRecord,
    OutcomeRecord,
    log_access,
    log_outcome,
)
from trowel_py.memory.north_star import memory_usage_metrics
from trowel_py.memory.promotion import evaluate_promotion
from trowel_py.memory.promotion_policy import PromotionPolicy
from trowel_py.memory.sessions_repo import (
    SessionRecord,
    create_sessions_repository,
    open_sessions_db,
)
from trowel_py.memory.store import MemoryStore

TZ = timezone.utc


def _seed(root: Path, cc: str, kind: str = "user") -> None:
    conn = open_sessions_db(root)
    try:
        create_sessions_repository(conn).register(
            SessionRecord(
                cc_session_id=cc,
                workdir="/p",
                date="2026-07-01",
                registered_at="t",
                session_kind=kind,
                trowel_session_id=f"t-{cc}",
            )
        )
    finally:
        conn.close()


def _read(root: Path, cc: str, stem: str, *, read_id: str, day: int = 1) -> None:
    log_access(
        root,
        AccessRecord(
            ts=f"2026-07-{day:02d}T10:00:00+00:00",
            trowel_session_id=f"t-{cc}",
            cc_session_id=cc,
            toolUseId="tu",
            action="read",
            search_id="s",
            read_id=read_id,
            memory_id=stem,
        ),
    )


def _outcome(root: Path, cc: str, stem: str, outcome: str, *, read_id: str) -> None:
    log_outcome(
        root,
        OutcomeRecord(
            ts="2026-07-01T10:01:00+00:00",
            trowel_session_id=f"t-{cc}",
            cc_session_id=cc,
            toolUseId="tu",
            read_id=read_id,
            memory_id=stem,
            outcome=outcome,
        ),
    )


def test_unsafe_memory_id_does_not_overwrite_core_md(tmp_path: Path) -> None:
    MemoryStore(tmp_path).write_note(
        {
            "type": "note",
            "title": "evil",
            "verification": "verified",
            "memory_id": "../../core",
            "kind": "gotcha",
            "__body": "x",
        }
    )
    _seed(tmp_path, "u1")
    _read(tmp_path, "u1", "evil", read_id="r1")
    _outcome(tmp_path, "u1", "evil", "helpful", read_id="r1")
    policy = PromotionPolicy(min_helpful_sessions=0, min_distinct_days=0)
    report = evaluate_promotion(tmp_path, policy, local_tz=TZ, today="2026-07-11")
    assert report["candidates"] == []
    assert not (tmp_path / "core.md").exists()


def test_from_dict_rejects_inferred_untested() -> None:
    with pytest.raises(ValueError):
        PromotionPolicy.from_dict({"allowed_verification": ["inferred-untested"]})


def test_search_calls_excludes_candidate_records(tmp_path: Path) -> None:
    _seed(tmp_path, "u1")
    log_access(
        tmp_path,
        AccessRecord(
            ts="2026-07-01T10:00:00+00:00",
            trowel_session_id="t-u1",
            cc_session_id="u1",
            toolUseId="tu",
            action="search",
            search_id="s",
            query="q",
            memory_id="",
            rank=None,
        ),
    )
    for i in range(2):
        log_access(
            tmp_path,
            AccessRecord(
                ts="2026-07-01T10:00:00+00:00",
                trowel_session_id="t-u1",
                cc_session_id="u1",
                toolUseId=f"tu{i}",
                action="search",
                search_id="s",
                query="q",
                memory_id=f"m{i}",
                rank=i,
            ),
        )
    retr = memory_usage_metrics(tmp_path)["retrieval"]
    assert retr["search_calls"] == 1
    assert retr["search_hits"] == 2


def test_metrics_no_side_effect_on_empty_root(tmp_path: Path) -> None:
    before = sorted(p.relative_to(tmp_path) for p in tmp_path.rglob("*"))
    memory_usage_metrics(tmp_path)
    after = sorted(p.relative_to(tmp_path) for p in tmp_path.rglob("*"))
    assert before == after
