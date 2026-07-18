"""slice-065 evaluate_promotion: policy-driven candidate generation."""
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
from trowel_py.memory.promotion import evaluate_promotion
from trowel_py.memory.promotion_policy import PromotionPolicy
from trowel_py.memory.sessions_repo import (
    SessionRecord,
    create_sessions_repository,
    open_sessions_db,
)
from trowel_py.memory.store import MemoryStore, _split_frontmatter

TZ = timezone.utc


def _note(
    root: Path,
    stem: str,
    *,
    memory_id: str | None = None,
    kind: str = "gotcha",
    verification: str = "verified",
) -> None:
    MemoryStore(root).write_note(
        {
            "type": "note",
            "title": stem,
            "verification": verification,
            "memory_id": memory_id or stem,
            "kind": kind,
            "__body": "body",
        }
    )


def _seed_kind(root: Path, cc: str, kind: str = "user") -> None:
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
            outcome=outcome,  # type: ignore[arg-type]
        ),
    )


def _helpful_sessions(root: Path, stem: str, ccs: tuple[tuple[str, int], ...]) -> None:
    for cc, day in ccs:
        _seed_kind(root, cc)
        _read(root, cc, stem, read_id=f"r{cc}", day=day)
        _outcome(root, cc, stem, "helpful", read_id=f"r{cc}")


def _gap(report: dict, memory_id: str) -> dict:
    return next(g for g in report["gaps"] if g["memory_id"] == memory_id)


def test_default_policy_no_candidate_when_below_threshold(tmp_path: Path) -> None:
    _note(tmp_path, "a")
    _helpful_sessions(tmp_path, "a", (("u1", 1), ("u2", 2)))  # 2 helpful, default needs 3
    report = evaluate_promotion(tmp_path, local_tz=TZ, today="2026-07-11")
    assert report["candidates"] == []
    gap = _gap(report, "a")
    assert "helpful_sessions" in gap["gaps"]
    assert gap["helpful_sessions"] == 2


def test_low_threshold_policy_generates_candidate(tmp_path: Path) -> None:
    _note(tmp_path, "a")
    _helpful_sessions(tmp_path, "a", (("u1", 1), ("u2", 2)))
    policy = PromotionPolicy(min_helpful_sessions=2, min_distinct_days=2)
    report = evaluate_promotion(tmp_path, policy, local_tz=TZ, today="2026-07-11")
    assert "a" in report["candidates"]
    cand = tmp_path / "meta" / "core-candidates" / "a.md"
    assert cand.exists()
    fm, body = _split_frontmatter(cand.read_text(encoding="utf-8"))
    assert fm["status"] == "candidate"
    assert fm["policy_version"] == policy.version
    assert fm["helpful_sessions"] == 2
    assert fm["distinct_days"] == 2
    assert fm["helpful_session_ids_hash"]  # provenance hash present
    assert "晋升依据" in body


def test_candidate_idempotent_upsert(tmp_path: Path) -> None:
    _note(tmp_path, "a")
    _helpful_sessions(tmp_path, "a", (("u1", 1), ("u2", 2), ("u3", 3)))
    policy = PromotionPolicy(min_helpful_sessions=2, min_distinct_days=1)
    evaluate_promotion(tmp_path, policy, local_tz=TZ, today="2026-07-11")
    evaluate_promotion(tmp_path, policy, local_tz=TZ, today="2026-07-11")
    files = list((tmp_path / "meta" / "core-candidates").glob("*.md"))
    assert len(files) == 1  # never stacks duplicates


def test_harmful_blocks_existing_candidate(tmp_path: Path) -> None:
    _note(tmp_path, "a")
    _helpful_sessions(tmp_path, "a", (("u1", 1), ("u2", 2), ("u3", 3)))
    policy = PromotionPolicy(min_helpful_sessions=2, min_distinct_days=1)
    evaluate_promotion(tmp_path, policy, local_tz=TZ, today="2026-07-11")
    # a previously-valid candidate now gains harmful counter-evidence
    _seed_kind(tmp_path, "h1")
    _read(tmp_path, "h1", "a", read_id="rh1", day=4)
    _outcome(tmp_path, "h1", "a", "harmful", read_id="rh1")
    report = evaluate_promotion(tmp_path, policy, local_tz=TZ, today="2026-07-11")
    assert "a" in report["blocked"]
    assert "a" not in report["candidates"]
    fm, _ = _split_frontmatter(
        (tmp_path / "meta" / "core-candidates" / "a.md").read_text(encoding="utf-8")
    )
    assert fm["status"] == "blocked"


def test_wrong_kind_gap(tmp_path: Path) -> None:
    _note(tmp_path, "a", kind="fact")  # fact not in allowed_kinds
    _helpful_sessions(tmp_path, "a", (("u1", 1),))
    policy = PromotionPolicy(min_helpful_sessions=1, min_distinct_days=1)
    report = evaluate_promotion(tmp_path, policy, local_tz=TZ, today="2026-07-11")
    assert "kind" in _gap(report, "a")["gaps"]
    assert report["candidates"] == []


def test_inferred_untested_verification_blocks(tmp_path: Path) -> None:
    _note(tmp_path, "a", verification="inferred-untested")
    _helpful_sessions(tmp_path, "a", (("u1", 1),))
    policy = PromotionPolicy(min_helpful_sessions=1, min_distinct_days=1)
    report = evaluate_promotion(tmp_path, policy, local_tz=TZ, today="2026-07-11")
    assert "verification" in _gap(report, "a")["gaps"]


def test_policy_echoed_in_report(tmp_path: Path) -> None:
    _note(tmp_path, "a")
    policy = PromotionPolicy(min_helpful_sessions=5)
    report = evaluate_promotion(tmp_path, policy, today="2026-07-11")
    assert report["policy"] == policy.to_dict()


def test_never_writes_core_md(tmp_path: Path) -> None:
    _note(tmp_path, "a")
    _helpful_sessions(tmp_path, "a", (("u1", 1), ("u2", 2), ("u3", 3)))
    policy = PromotionPolicy(min_helpful_sessions=3, min_distinct_days=1)
    evaluate_promotion(tmp_path, policy, local_tz=TZ, today="2026-07-11")
    assert not (tmp_path / "core.md").exists()


def test_judgement_only_evidence_can_promote(tmp_path: Path) -> None:
    # no outcome-log at all; two segment judgements (different sessions) help.
    _note(tmp_path, "a")
    _seed_kind(tmp_path, "u1")
    _seed_kind(tmp_path, "u2")
    save_judgement_report(
        tmp_path,
        JudgementReport(
            cc_session_id="u1", hits=(HitJudgement(
                memory_id="a", used=True, outcome="helpful", reason="r", evidence="e"
            ),), recall_miss=(), summary="s", segment_id="u1-s1",
        ),
    )
    save_judgement_report(
        tmp_path,
        JudgementReport(
            cc_session_id="u2", hits=(HitJudgement(
                memory_id="a", used=True, outcome="helpful", reason="r", evidence="e"
            ),), recall_miss=(), summary="s", segment_id="u2-s1",
        ),
    )
    policy = PromotionPolicy(min_helpful_sessions=2, min_distinct_days=1)
    report = evaluate_promotion(tmp_path, policy, local_tz=TZ, today="2026-07-11")
    # helpful_sessions=2 but distinct_days=0 (judgement has no read dates) → gap
    gap = _gap(report, "a")
    assert gap["helpful_sessions"] == 2
    assert "distinct_days" in gap["gaps"]
    assert report["candidates"] == []
