"""slice-041 north-star metrics tests."""
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


# ---------- slice-053: memory_usage_metrics (three indicators) ----------

from trowel_py.memory.judgements import (  # noqa: E402
    HitJudgement,
    JudgementReport,
    MissJudgement,
    save_judgement_report,
)
from trowel_py.memory.north_star import memory_usage_metrics  # noqa: E402
from trowel_py.memory.sessions_repo import (  # noqa: E402
    SessionRecord,
    create_sessions_repository,
    open_sessions_db,
)


def _access(root: Path, cc: str, action: str, *, n: int = 1, query: str = "q") -> None:
    """Seed n access-log records under cc_session_id=cc.

    search records are CANDIDATES (carry memory_id+rank, like mcp_server's
    per-candidate record); a separate summary record is not seeded since it is
    not a "hit" and must not count toward read_rate's denominator.
    """
    for i in range(n):
        log_access(
            root,
            AccessRecord(
                ts="2026-07-16T10:00:00",
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
    cc: str, hits=(), recall_miss=(), summary: str = "s"
) -> JudgementReport:
    return JudgementReport(
        cc_session_id=cc,
        hits=tuple(hits),
        recall_miss=tuple(recall_miss),
        summary=summary,
    )


def test_read_rate_reads_over_searches(tmp_path: Path) -> None:
    # user session: 5 search candidates, 2 reads → read_rate = 2/5
    _seed_session_kind(tmp_path, "u1", "user")
    _access(tmp_path, "u1", "search", n=5)
    _access(tmp_path, "u1", "read", n=2)
    m = memory_usage_metrics(tmp_path)
    assert m["reads"] == 2
    assert m["search_hits"] == 5
    assert m["read_rate"] == round(2 / 5, 4)


def test_read_rate_excludes_eval_sessions(tmp_path: Path) -> None:
    # C-3: the judge's own eval-kind search/read must NOT count.
    # user: 4 search + 1 read → 1/4 ; eval: 100 search + 0 read (must be ignored)
    _seed_session_kind(tmp_path, "u1", "user")
    _seed_session_kind(tmp_path, "eval1", "eval")
    _seed_session_kind(tmp_path, "dist1", "distill")
    _access(tmp_path, "u1", "search", n=4)
    _access(tmp_path, "u1", "read", n=1)
    _access(tmp_path, "eval1", "search", n=100)
    _access(tmp_path, "dist1", "search", n=50)
    m = memory_usage_metrics(tmp_path)
    assert m["reads"] == 1
    assert m["search_hits"] == 4
    assert m["read_rate"] == round(1 / 4, 4)


def test_read_rate_none_when_no_searches(tmp_path: Path) -> None:
    _seed_session_kind(tmp_path, "u1", "user")
    _access(tmp_path, "u1", "read", n=3)  # reads but zero searches → undefined
    m = memory_usage_metrics(tmp_path)
    assert m["read_rate"] is None
    assert m["reads"] == 3
    assert m["search_hits"] == 0


def test_hit_quality_helpful_over_judged(tmp_path: Path) -> None:
    save_judgement_report(
        tmp_path,
        _report(
            "s1",
            hits=(_hit("a", "helpful"), _hit("b", "helpful"),
                  _hit("c", "harmful"), _hit("d", "unused")),
        ),
    )
    m = memory_usage_metrics(tmp_path)
    # helpful 2 / (helpful 2 + harmful 1 + unused 1) = 0.5 ; unknown excluded
    assert m["hit_quality"] == round(2 / 4, 4)
    assert m["hits_helpful"] == 2
    assert m["hits_harmful"] == 1
    assert m["hits_unused"] == 1


def test_hit_quality_none_when_all_unknown(tmp_path: Path) -> None:
    save_judgement_report(tmp_path, _report("s1", hits=(_hit("a", "unknown"),)))
    m = memory_usage_metrics(tmp_path)
    assert m["hit_quality"] is None  # no helpful/harmful/unused to divide


def test_recall_miss_rate_over_judged_sessions(tmp_path: Path) -> None:
    save_judgement_report(
        tmp_path,
        _report("s1", recall_miss=(_miss("a", "retrieval_miss"),
                                    _miss("b", "awareness_miss"))),
    )
    save_judgement_report(tmp_path, _report("s2", recall_miss=(_miss("c", "retrieval_miss"),)))
    m = memory_usage_metrics(tmp_path)
    # 3 misses / 2 judged sessions = 1.5
    assert m["recall_miss_rate"] == round(3 / 2, 4)
    assert m["retrieval_miss"] == 2
    assert m["awareness_miss"] == 1
    assert m["judged_sessions"] == 2
    # known_issue_repeat_rate approximated by recall_miss_rate (no longer None)
    assert m["known_issue_repeat_rate"] == m["recall_miss_rate"]


def test_recall_miss_rate_none_when_no_judgements(tmp_path: Path) -> None:
    m = memory_usage_metrics(tmp_path)
    assert m["recall_miss_rate"] is None
    assert m["known_issue_repeat_rate"] is None
    assert m["judged_sessions"] == 0


# ---------- slice-061: attribution coverage + trowel-binding resolution -----


def test_coverage_counts_unattributed(tmp_path: Path) -> None:
    """C-7: records with no verifiable mapping are unattributed (never guessed)."""
    _seed_session_kind(tmp_path, "u1", "user")
    _access(tmp_path, "u1", "search", n=2)  # attributed via cc_session_id
    log_access(
        tmp_path,
        AccessRecord(
            ts="t", trowel_session_id="", cc_session_id="",
            toolUseId="tu-x", action="search", search_id="s",
            query="q", memory_id="m", rank=0,
        ),
    )
    m = memory_usage_metrics(tmp_path)
    assert m["attributed"] == 2
    assert m["unattributed"] == 1
    assert m["coverage"] == round(2 / 3, 4)


def test_empty_cc_id_record_attributed_via_trowel_binding(tmp_path: Path) -> None:
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
    # pre-init records: cc_session_id empty, trowel_session_id known
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
    assert m["search_hits"] == 1
    assert m["reads"] == 1
    assert m["read_rate"] == 1.0
    assert m["unattributed"] == 0
    assert m["coverage"] == 1.0
