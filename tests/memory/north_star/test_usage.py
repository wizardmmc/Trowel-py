from __future__ import annotations

from pathlib import Path

from trowel_py.memory.access_log import AccessRecord, log_access
from trowel_py.memory.judgements import save_judgement_report
from trowel_py.memory.north_star import memory_usage_metrics
from trowel_py.memory.promotion_policy import PromotionPolicy
from trowel_py.memory.sessions_repo import (
    SessionRecord,
    create_sessions_repository,
    open_sessions_db,
)

from .support import hit, log_records, miss, report, seed_session, write_note


def test_retrieval_read_rate_with_numerator_denominator(tmp_path: Path) -> None:
    seed_session(tmp_path, "u1")
    log_records(tmp_path, "u1", "search", count=5)
    log_records(tmp_path, "u1", "read", count=2)

    retrieval = memory_usage_metrics(tmp_path)["retrieval"]

    assert retrieval["reads"] == 2
    assert retrieval["search_hits"] == 5
    assert retrieval["read_rate"] == round(2 / 5, 4)
    assert retrieval["read_rate_numerator"] == 2
    assert retrieval["read_rate_denominator"] == 5


def test_retrieval_excludes_eval_sessions(tmp_path: Path) -> None:
    seed_session(tmp_path, "u1")
    seed_session(tmp_path, "eval1", "eval")
    seed_session(tmp_path, "dist1", "distill")
    log_records(tmp_path, "u1", "search", count=4)
    log_records(tmp_path, "u1", "read")
    log_records(tmp_path, "eval1", "search", count=100)
    log_records(tmp_path, "dist1", "search", count=50)

    retrieval = memory_usage_metrics(tmp_path)["retrieval"]

    assert retrieval["reads"] == 1
    assert retrieval["search_hits"] == 4


def test_retrieval_read_rate_none_when_no_searches(tmp_path: Path) -> None:
    seed_session(tmp_path, "u1")
    log_records(tmp_path, "u1", "read", count=3)

    retrieval = memory_usage_metrics(tmp_path)["retrieval"]

    assert retrieval["read_rate"] is None
    assert retrieval["reads"] == 3
    assert retrieval["search_hits"] == 0


def test_effect_hit_quality_session_level(tmp_path: Path) -> None:
    for memory_id in ("a", "b", "c", "d"):
        write_note(tmp_path, memory_id)
    seed_session(tmp_path, "s1")
    save_judgement_report(
        tmp_path,
        report(
            "s1",
            hits=(
                hit("a", "helpful"),
                hit("b", "helpful"),
                hit("c", "harmful"),
                hit("d", "unused"),
            ),
        ),
    )

    effect = memory_usage_metrics(tmp_path)["effect"]

    assert effect["hit_quality"] == round(2 / 4, 4)
    assert effect["helpful_sessions"] == 2
    assert effect["harmful_sessions"] == 1
    assert effect["unused_sessions"] == 1
    assert effect["hit_quality_numerator"] == 2
    assert effect["hit_quality_denominator"] == 4


def test_effect_hit_quality_none_when_all_unknown(tmp_path: Path) -> None:
    write_note(tmp_path, "a")
    seed_session(tmp_path, "s1")
    save_judgement_report(
        tmp_path,
        report("s1", hits=(hit("a", "unknown"),)),
    )

    assert memory_usage_metrics(tmp_path)["effect"]["hit_quality"] is None


def test_recall_miss_rate_over_judged_user_sessions(tmp_path: Path) -> None:
    for memory_id in ("a", "b", "c"):
        write_note(tmp_path, memory_id)
    seed_session(tmp_path, "s1")
    seed_session(tmp_path, "s2")
    save_judgement_report(
        tmp_path,
        report(
            "s1",
            recall_miss=(
                miss("a", "retrieval_miss"),
                miss("b", "awareness_miss"),
            ),
        ),
    )
    save_judgement_report(
        tmp_path,
        report("s2", recall_miss=(miss("c", "retrieval_miss"),)),
    )

    recall = memory_usage_metrics(tmp_path)["recall"]

    assert recall["recall_miss_rate"] == round(3 / 2, 4)
    assert recall["retrieval_miss"] == 2
    assert recall["awareness_miss"] == 1
    assert recall["recall_miss_rate_denominator"] == 2


def test_known_issue_repeat_rate_is_null_not_recall_proxy(tmp_path: Path) -> None:
    metrics = memory_usage_metrics(tmp_path)

    assert metrics["known_issue_repeat_rate"] is None
    assert metrics["recall"]["recall_miss_rate"] is None


def test_identity_counts_unattributed(tmp_path: Path) -> None:
    seed_session(tmp_path, "u1")
    log_records(tmp_path, "u1", "search", count=2)
    log_access(
        tmp_path,
        AccessRecord(
            ts="t",
            trowel_session_id="",
            cc_session_id="",
            toolUseId="tu-x",
            action="search",
            search_id="s",
            query="q",
            memory_id="m",
            rank=0,
        ),
    )

    identity = memory_usage_metrics(tmp_path)["identity"]

    assert identity["attributed"] == 2
    assert identity["unattributed"] == 1
    assert identity["records_total"] == 3
    assert identity["coverage"] == round(2 / 3, 4)


def test_identity_resolves_via_trowel_binding(tmp_path: Path) -> None:
    conn = open_sessions_db(tmp_path)
    try:
        create_sessions_repository(conn).register(
            SessionRecord(
                cc_session_id="u1",
                workdir="/project",
                date="2026-07-16",
                registered_at="t",
                session_kind="user",
                trowel_session_id="t1",
            )
        )
    finally:
        conn.close()
    log_access(
        tmp_path,
        AccessRecord(
            ts="t",
            trowel_session_id="t1",
            cc_session_id="",
            toolUseId="tu-1",
            action="search",
            search_id="s",
            query="q",
            memory_id="m0",
            rank=0,
        ),
    )
    log_access(
        tmp_path,
        AccessRecord(
            ts="t",
            trowel_session_id="t1",
            cc_session_id="",
            toolUseId="tu-2",
            action="read",
            search_id="",
            read_id="r",
            memory_id="m0",
        ),
    )

    metrics = memory_usage_metrics(tmp_path)

    assert metrics["retrieval"]["search_hits"] == 1
    assert metrics["retrieval"]["reads"] == 1
    assert metrics["retrieval"]["read_rate"] == 1.0
    assert metrics["identity"]["unattributed"] == 0
    assert metrics["identity"]["coverage"] == 1.0


def test_quality_insufficient_with_no_data(tmp_path: Path) -> None:
    metrics = memory_usage_metrics(tmp_path)

    assert metrics["identity"]["quality"] == "insufficient"
    assert metrics["retrieval"]["quality"] == "insufficient"
    assert metrics["effect"]["quality"] == "insufficient"
    assert metrics["recall"]["quality"] == "insufficient"


def test_quality_uses_injected_policy_thresholds(tmp_path: Path) -> None:
    policy = PromotionPolicy(
        min_identity_coverage_reliable=0.0,
        min_identity_sample_reliable=1,
    )
    seed_session(tmp_path, "u1")
    log_records(tmp_path, "u1", "search", count=2)

    metrics = memory_usage_metrics(tmp_path, policy=policy)

    assert metrics["identity"]["quality"] == "reliable"
    assert metrics["policy"] == policy.to_dict()


def test_quality_partial_when_sample_below_default_threshold(tmp_path: Path) -> None:
    seed_session(tmp_path, "u1")
    log_records(tmp_path, "u1", "search", count=2)

    assert memory_usage_metrics(tmp_path)["identity"]["quality"] == "partial"
