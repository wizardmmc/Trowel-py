"""slice-041 north-star metrics + slice-065 coverage/quality metrics tests."""
from __future__ import annotations

from pathlib import Path

from trowel_py.memory.access_log import AccessRecord, OutcomeRecord, log_access, log_outcome
from trowel_py.memory.north_star import compute_north_star
from trowel_py.memory.store import MemoryStore


def _note(root: Path, mid: str, *, status: str = "active", harmful_refs: int = 0) -> str:
    return MemoryStore(root).write_note({
        "type": "note", "title": f"n-{mid}", "verification": "verified",
        "memory_id": mid, "status": status, "harmful_refs": harmful_refs,
        "__body": "x",
    })


def test_harmful_rate_zero_when_all_active_clean(tmp_path: Path) -> None:
    _note(tmp_path, "a", status="active")
    _note(tmp_path, "b", status="active")
    m = compute_north_star(tmp_path, today="2026-07-11")
    assert m["harmful_memory_rate"] == 0.0
    assert m["active_notes"] == 2
    assert m["known_issue_repeat_rate"] is None  # TODO


def test_harmful_rate_counts_contradicted(tmp_path: Path) -> None:
    _note(tmp_path, "a", status="active")
    _note(tmp_path, "b", status="active")
    _note(tmp_path, "c", status="contradicted")
    _note(tmp_path, "d", status="superseded")
    m = compute_north_star(tmp_path, today="2026-07-11")
    # W6: 2 flagged / 4 non-retired = 0.5 (same population, not 2/2 active = 1.0)
    assert m["contradicted_or_superseded"] == 2
    assert m["active_notes"] == 2
    assert m["harmful_memory_rate"] == 0.5


def test_harmful_rate_never_exceeds_one_when_corrections_accumulate(tmp_path: Path) -> None:
    """W6 (codex): corrections accumulate over time; the rate must stay <=1.0
    because numerator and denominator share the same non-retired population."""
    # 2 active, 5 contradicted (historical corrections piled up)
    for i in range(2):
        _note(tmp_path, f"a{i}", status="active")
    for i in range(5):
        _note(tmp_path, f"c{i}", status="contradicted")
    m = compute_north_star(tmp_path, today="2026-07-11")
    # 5 contradicted / 7 non-retired ~= 0.7143 (NOT 5/2 = 2.5)
    assert m["harmful_memory_rate"] <= 1.0
    assert m["harmful_memory_rate"] == round(5 / 7, 4)


def test_harmful_rate_counts_high_harmful_refs(tmp_path: Path) -> None:
    _note(tmp_path, "a", status="active", harmful_refs=5)  # ≥ threshold (3)
    _note(tmp_path, "b", status="active", harmful_refs=1)  # below
    m = compute_north_star(tmp_path, today="2026-07-11")
    # 1 harmful_high / 2 active = 0.5
    assert m["harmful_high_notes"] == 1
    assert m["harmful_memory_rate"] == 0.5


def test_metrics_carry_raw_log_material(tmp_path: Path) -> None:
    _note(tmp_path, "a")
    log_access(tmp_path, AccessRecord(
        ts="2026-07-01T10:00:00+00:00", trowel_session_id="t", cc_session_id="c",
        toolUseId="tu", action="read", search_id="s", read_id="r", memory_id="a",
    ))
    log_outcome(tmp_path, OutcomeRecord(
        ts="2026-07-01T10:01:00+00:00", trowel_session_id="t", cc_session_id="c",
        toolUseId="tu", read_id="r", memory_id="a", outcome="harmful",
    ))
    m = compute_north_star(tmp_path, today="2026-07-11")
    assert m["raw_reads"] == 1
    assert m["raw_harmful_outcomes"] == 1


def test_no_notes_does_not_divide_by_zero(tmp_path: Path) -> None:
    m = compute_north_star(tmp_path, today="2026-07-11")
    assert m["harmful_memory_rate"] == 0.0
    assert m["active_notes"] == 0


# ---------- slice-065: memory_usage_metrics (coverage + quality + sessions) ---

from trowel_py.memory.judgements import (  # noqa: E402
    HitJudgement,
    JudgementReport,
    MissJudgement,
    save_judgement_report,
)
from trowel_py.memory.north_star import memory_usage_metrics  # noqa: E402
from trowel_py.memory.promotion_policy import PromotionPolicy  # noqa: E402
from trowel_py.memory.sessions_repo import (  # noqa: E402
    SessionRecord,
    create_sessions_repository,
    open_sessions_db,
)


def _access(root: Path, cc: str, action: str, *, n: int = 1, query: str = "q") -> None:
    """Seed n access-log records under cc_session_id=cc (trowel id falls back
    to the cc so attribution resolves via the cc_session_id path)."""
    for i in range(n):
        log_access(
            root,
            AccessRecord(
                ts="2026-07-16T10:00:00+00:00",
                trowel_session_id="t",
                cc_session_id=cc,
                toolUseId=f"tu-{cc}-{action}-{i}",
                action=action,  # type: ignore[arg-type]
                search_id="s" if action == "search" else "",
                read_id="r" if action == "read" else "",
                query=query if action == "search" else "",
                memory_id=f"m-{i}" if action in ("search", "read") else "",
                rank=i if action == "search" else None,
            ),
        )


def _seed_session_kind(root: Path, cc_id: str, kind: str = "user") -> None:
    conn = open_sessions_db(root)
    try:
        create_sessions_repository(conn).register(
            SessionRecord(
                cc_session_id=cc_id,
                workdir="/p",
                date="2026-07-16",
                registered_at="2026-07-16T10:00:00",
                session_kind=kind,
            )
        )
    finally:
        conn.close()


def _note_for(root: Path, mid: str) -> str:
    # a real note backing a judgement hit's memory_id (C-6 — fabricated ids drop).
    return MemoryStore(root).write_note({
        "type": "note", "title": f"n-{mid}", "verification": "verified",
        "memory_id": mid, "__body": "x",
    })


def _hit(mid: str, outcome: str, used: bool = True) -> HitJudgement:
    return HitJudgement(
        memory_id=mid, used=used, outcome=outcome,  # type: ignore[arg-type]
        reason="r", evidence="e",
    )


def _miss(mid: str, attribution: str) -> MissJudgement:
    return MissJudgement(
        memory_id=mid, attribution=attribution,  # type: ignore[arg-type]
        reason="r", evidence="e",
    )


def _report(
    cc: str, hits=(), recall_miss=(), summary: str = "s", segment: str = ""
) -> JudgementReport:
    return JudgementReport(
        cc_session_id=cc,
        hits=tuple(hits),
        recall_miss=tuple(recall_miss),
        summary=summary,
        segment_id=segment,
    )


# ---- retrieval ----


def test_retrieval_read_rate_with_numerator_denominator(tmp_path: Path) -> None:
    _seed_session_kind(tmp_path, "u1")
    _access(tmp_path, "u1", "search", n=5)
    _access(tmp_path, "u1", "read", n=2)
    retr = memory_usage_metrics(tmp_path)["retrieval"]
    assert retr["reads"] == 2
    assert retr["search_hits"] == 5
    assert retr["read_rate"] == round(2 / 5, 4)
    assert retr["read_rate_numerator"] == 2
    assert retr["read_rate_denominator"] == 5


def test_retrieval_excludes_eval_sessions(tmp_path: Path) -> None:
    # C-4: the judge's own eval/distill search/read must NOT count.
    _seed_session_kind(tmp_path, "u1")
    _seed_session_kind(tmp_path, "eval1", "eval")
    _seed_session_kind(tmp_path, "dist1", "distill")
    _access(tmp_path, "u1", "search", n=4)
    _access(tmp_path, "u1", "read", n=1)
    _access(tmp_path, "eval1", "search", n=100)
    _access(tmp_path, "dist1", "search", n=50)
    retr = memory_usage_metrics(tmp_path)["retrieval"]
    assert retr["reads"] == 1
    assert retr["search_hits"] == 4


def test_retrieval_read_rate_none_when_no_searches(tmp_path: Path) -> None:
    _seed_session_kind(tmp_path, "u1")
    _access(tmp_path, "u1", "read", n=3)
    retr = memory_usage_metrics(tmp_path)["retrieval"]
    assert retr["read_rate"] is None
    assert retr["reads"] == 3
    assert retr["search_hits"] == 0


# ---- effect ----


def test_effect_hit_quality_session_level(tmp_path: Path) -> None:
    for mid in ("a", "b", "c", "d"):
        _note_for(tmp_path, mid)
    _seed_session_kind(tmp_path, "s1")
    save_judgement_report(
        tmp_path,
        _report(
            "s1",
            hits=(_hit("a", "helpful"), _hit("b", "helpful"),
                  _hit("c", "harmful"), _hit("d", "unused")),
        ),
    )
    eff = memory_usage_metrics(tmp_path)["effect"]
    # 2 helpful / (2 helpful + 1 harmful + 1 unused) = 0.5 ; unknown excluded
    assert eff["hit_quality"] == round(2 / 4, 4)
    assert eff["helpful_sessions"] == 2
    assert eff["harmful_sessions"] == 1
    assert eff["unused_sessions"] == 1
    assert eff["hit_quality_numerator"] == 2
    assert eff["hit_quality_denominator"] == 4


def test_effect_hit_quality_none_when_all_unknown(tmp_path: Path) -> None:
    _note_for(tmp_path, "a")
    _seed_session_kind(tmp_path, "s1")
    save_judgement_report(tmp_path, _report("s1", hits=(_hit("a", "unknown"),)))
    assert memory_usage_metrics(tmp_path)["effect"]["hit_quality"] is None


# ---- recall ----


def test_recall_miss_rate_over_judged_user_sessions(tmp_path: Path) -> None:
    for mid in ("a", "b", "c"):
        _note_for(tmp_path, mid)
    _seed_session_kind(tmp_path, "s1")
    _seed_session_kind(tmp_path, "s2")
    save_judgement_report(
        tmp_path,
        _report("s1", recall_miss=(_miss("a", "retrieval_miss"),
                                    _miss("b", "awareness_miss"))),
    )
    save_judgement_report(tmp_path, _report("s2", recall_miss=(_miss("c", "retrieval_miss"),)))
    rec = memory_usage_metrics(tmp_path)["recall"]
    assert rec["recall_miss_rate"] == round(3 / 2, 4)
    assert rec["retrieval_miss"] == 2
    assert rec["awareness_miss"] == 1
    assert rec["recall_miss_rate_denominator"] == 2


def test_known_issue_repeat_rate_is_null_not_recall_proxy(tmp_path: Path) -> None:
    # C-6: the strict metric stays null; recall_miss_rate lives under its own name.
    m = memory_usage_metrics(tmp_path)
    assert m["known_issue_repeat_rate"] is None
    assert m["recall"]["recall_miss_rate"] is None  # no judgements either


# ---- identity (slice-061) ----


def test_identity_counts_unattributed(tmp_path: Path) -> None:
    """C-7: records with no verifiable mapping are unattributed (never guessed)."""
    _seed_session_kind(tmp_path, "u1")
    _access(tmp_path, "u1", "search", n=2)  # attributed via cc_session_id
    log_access(
        tmp_path,
        AccessRecord(
            ts="t", trowel_session_id="", cc_session_id="",
            toolUseId="tu-x", action="search", search_id="s",
            query="q", memory_id="m", rank=0,
        ),
    )
    ident = memory_usage_metrics(tmp_path)["identity"]
    assert ident["attributed"] == 2
    assert ident["unattributed"] == 1
    assert ident["records_total"] == 3
    assert ident["coverage"] == round(2 / 3, 4)


def test_identity_resolves_via_trowel_binding(tmp_path: Path) -> None:
    """C-3: a fresh-session record (cc_id empty, written pre-init) re-enters the
    user population once its trowel binding lands."""
    conn = open_sessions_db(tmp_path)
    try:
        create_sessions_repository(conn).register(
            SessionRecord(
                cc_session_id="u1", workdir="/p", date="2026-07-16",
                registered_at="t", session_kind="user", trowel_session_id="t1",
            )
        )
    finally:
        conn.close()
    log_access(
        tmp_path,
        AccessRecord(
            ts="t", trowel_session_id="t1", cc_session_id="",
            toolUseId="tu-1", action="search", search_id="s",
            query="q", memory_id="m0", rank=0,
        ),
    )
    log_access(
        tmp_path,
        AccessRecord(
            ts="t", trowel_session_id="t1", cc_session_id="",
            toolUseId="tu-2", action="read", search_id="", read_id="r",
            memory_id="m0",
        ),
    )
    m = memory_usage_metrics(tmp_path)
    assert m["retrieval"]["search_hits"] == 1
    assert m["retrieval"]["reads"] == 1
    assert m["retrieval"]["read_rate"] == 1.0
    assert m["identity"]["unattributed"] == 0
    assert m["identity"]["coverage"] == 1.0


# ---- quality labels (slice-065 C-5) ----


def test_quality_insufficient_with_no_data(tmp_path: Path) -> None:
    m = memory_usage_metrics(tmp_path)
    assert m["identity"]["quality"] == "insufficient"
    assert m["retrieval"]["quality"] == "insufficient"
    assert m["effect"]["quality"] == "insufficient"
    assert m["recall"]["quality"] == "insufficient"


def test_quality_uses_injected_policy_thresholds(tmp_path: Path) -> None:
    # zero thresholds → even one attributed record is reliable.
    p = PromotionPolicy(
        min_identity_coverage_reliable=0.0, min_identity_sample_reliable=1
    )
    _seed_session_kind(tmp_path, "u1")
    _access(tmp_path, "u1", "search", n=2)
    m = memory_usage_metrics(tmp_path, policy=p)
    assert m["identity"]["quality"] == "reliable"
    assert m["policy"] == p.to_dict()


def test_quality_partial_when_sample_below_default_threshold(tmp_path: Path) -> None:
    # default threshold (20) > 2 records → partial (data exists, not enough).
    _seed_session_kind(tmp_path, "u1")
    _access(tmp_path, "u1", "search", n=2)
    assert memory_usage_metrics(tmp_path)["identity"]["quality"] == "partial"
