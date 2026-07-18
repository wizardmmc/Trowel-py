"""slice-065 compute_note_effects: two-source, session-level evidence.

Covers the §测试方法 matrix: explicit outcome, judge fallback, conflict,
repeat read, repeat segment, resume (two segments, one session), multi
session, multi day, non-user exclusion, unattributed, plus the robustness
backstops (outcome without a matching read, fabricated memory_id, unused hit).
"""
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
        memory_id=mid, used=used, outcome=outcome,  # type: ignore[arg-type]
        reason="r", evidence="e",
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


# ---------- §1 the two evidence sources ----------


def test_explicit_outcome_is_a_helpful_session(tmp_path: Path) -> None:
    _note(tmp_path, "a")
    _seed_kind(tmp_path, "u1")
    _read(tmp_path, "u1", "a", read_id="r1")
    _outcome(tmp_path, "u1", "a", "helpful", read_id="r1")
    fx = _fx(tmp_path)
    assert fx["a"].helpful_refs == 1
    assert "u1" in fx["a"].helpful_sessions
    assert fx["a"].harmful_refs == 0


def test_judge_fallback_counts_without_outcome(tmp_path: Path) -> None:
    # §通过标准: helpful evidence no longer depends on outcome-log alone.
    _note(tmp_path, "a")
    _seed_kind(tmp_path, "u1")
    _judge(tmp_path, "u1", (_hit("a", "helpful"),))
    fx = _fx(tmp_path)
    assert fx["a"].helpful_refs == 1


def test_conflict_helpful_and_harmful_resolves_harmful(tmp_path: Path) -> None:
    # §通过标准: same session helpful + harmful → harmful; helpful swallowed.
    _note(tmp_path, "a")
    _seed_kind(tmp_path, "u1")
    _read(tmp_path, "u1", "a", read_id="r1")
    _outcome(tmp_path, "u1", "a", "helpful", read_id="r1")
    _judge(tmp_path, "u1", (_hit("a", "harmful"),))
    fx = _fx(tmp_path)
    assert fx["a"].harmful_refs == 1
    assert fx["a"].helpful_refs == 0


# ---------- C-2 independent-session counting ----------


def test_repeat_read_same_session_is_one_session(tmp_path: Path) -> None:
    # §通过标准: ten reads + repeated judgement in one session → one helpful.
    _note(tmp_path, "a")
    _seed_kind(tmp_path, "u1")
    for i in range(10):
        _read(tmp_path, "u1", "a", read_id=f"r{i}")
    _judge(tmp_path, "u1", (_hit("a", "helpful"),))
    fx = _fx(tmp_path)
    assert fx["a"].refs == 10
    assert fx["a"].read_session_count == 1
    assert fx["a"].helpful_refs == 1


def test_resume_two_segments_one_session(tmp_path: Path) -> None:
    # same cc, two segment judgements (resume) → still one helpful session.
    _note(tmp_path, "a")
    _seed_kind(tmp_path, "u1")
    _judge(tmp_path, "u1", (_hit("a", "helpful"),), segment="seg1")
    _judge(tmp_path, "u1", (_hit("a", "helpful"),), segment="seg2")
    fx = _fx(tmp_path)
    assert fx["a"].helpful_refs == 1


def test_repeat_same_segment_idempotent(tmp_path: Path) -> None:
    # re-running the same segment upserts the file → not double-counted.
    _note(tmp_path, "a")
    _seed_kind(tmp_path, "u1")
    _judge(tmp_path, "u1", (_hit("a", "helpful"),), segment="seg1")
    _judge(tmp_path, "u1", (_hit("a", "helpful"),), segment="seg1")
    fx = _fx(tmp_path)
    assert fx["a"].helpful_refs == 1


def test_multiple_sessions_each_count(tmp_path: Path) -> None:
    _note(tmp_path, "a")
    for cc in ("u1", "u2", "u3"):
        _seed_kind(tmp_path, cc)
        _read(tmp_path, cc, "a", read_id=f"r{cc}")
        _outcome(tmp_path, cc, "a", "helpful", read_id=f"r{cc}")
    fx = _fx(tmp_path)
    assert fx["a"].helpful_refs == 3


def test_multiple_days_distinct(tmp_path: Path) -> None:
    _note(tmp_path, "a")
    _seed_kind(tmp_path, "u1")
    _seed_kind(tmp_path, "u2")
    _read(tmp_path, "u1", "a", read_id="r1", ts="2026-07-01T10:00:00+00:00")
    _outcome(tmp_path, "u1", "a", "helpful", read_id="r1")
    _read(tmp_path, "u2", "a", read_id="r2", ts="2026-07-05T10:00:00+00:00")
    _outcome(tmp_path, "u2", "a", "helpful", read_id="r2")
    fx = _fx(tmp_path)
    assert fx["a"].distinct_days == 2  # helpful evidence spans 2 days
    assert fx["a"].last_ref == "2026-07-05"


def test_distinct_days_counts_only_helpful_evidence(tmp_path: Path) -> None:
    # C-7: a next-day unknown read must NOT make single-day helpful evidence
    # look multi-day. distinct_days counts only helpful sessions' read dates.
    _note(tmp_path, "a")
    _seed_kind(tmp_path, "u1")
    _seed_kind(tmp_path, "u2")
    _read(tmp_path, "u1", "a", read_id="r1", ts="2026-07-01T10:00:00+00:00")
    _outcome(tmp_path, "u1", "a", "helpful", read_id="r1")
    _read(tmp_path, "u2", "a", read_id="r2", ts="2026-07-02T10:00:00+00:00")
    # u2 read with no outcome (unknown) — not helpful evidence
    fx = _fx(tmp_path)
    assert fx["a"].distinct_days == 1
    assert fx["a"].last_ref == "2026-07-02"  # last_ref still sees ALL reads


# ---------- C-4 non-user exclusion + unattributed ----------


def test_eval_session_excluded_from_everything(tmp_path: Path) -> None:
    # §通过标准: user helpful mixed with eval helpful → only user counts.
    _note(tmp_path, "a")
    _seed_kind(tmp_path, "u1")
    _seed_kind(tmp_path, "e1", "eval")
    _read(tmp_path, "u1", "a", read_id="ru")
    _outcome(tmp_path, "u1", "a", "helpful", read_id="ru")
    _read(tmp_path, "e1", "a", read_id="re")
    _outcome(tmp_path, "e1", "a", "helpful", read_id="re")
    _judge(tmp_path, "e1", (_hit("a", "helpful"),))
    fx = _fx(tmp_path)
    assert fx["a"].refs == 1  # only the user read
    assert fx["a"].helpful_refs == 1  # only the user session
    assert fx["a"].read_sessions == frozenset({"u1"})


def test_unattributed_record_excluded(tmp_path: Path) -> None:
    _note(tmp_path, "a")
    # no trowel id, no cc id → unattributed (C-7: never guessed into user).
    log_access(
        tmp_path,
        AccessRecord(
            ts="2026-07-01T10:00:00+00:00",
            trowel_session_id="",
            cc_session_id="",
            toolUseId="tu",
            action="read",
            search_id="s",
            read_id="rx",
            memory_id="a",
        ),
    )
    assert "a" not in _fx(tmp_path)


# ---------- robustness backstops ----------


def test_outcome_without_matching_read_dropped(tmp_path: Path) -> None:
    # §1: explicit evidence must relate to a real read_id.
    _note(tmp_path, "a")
    _seed_kind(tmp_path, "u1")
    _outcome(tmp_path, "u1", "a", "helpful", read_id="ghost")
    assert "a" not in _fx(tmp_path)


def test_outcome_from_eval_session_quoting_user_read_dropped(tmp_path: Path) -> None:
    # C-4: a non-user session must not inject evidence by quoting a user's
    # read_id. The user read alone gives no effect; the eval outcome is dropped.
    _note(tmp_path, "a")
    _seed_kind(tmp_path, "u1")
    _seed_kind(tmp_path, "e1", "eval")
    _read(tmp_path, "u1", "a", read_id="r1")
    log_outcome(
        tmp_path,
        OutcomeRecord(
            ts="2026-07-01T10:01:00+00:00",
            trowel_session_id="t-e1",
            cc_session_id="e1",
            toolUseId="tu",
            read_id="r1",
            memory_id="a",
            outcome="helpful",
        ),
    )
    fx = _fx(tmp_path)
    assert fx["a"].helpful_refs == 0


def test_judge_fabricated_memory_id_dropped(tmp_path: Path) -> None:
    _note(tmp_path, "a")
    _seed_kind(tmp_path, "u1")
    _judge(tmp_path, "u1", (_hit("ghost-uuid", "helpful"),))
    assert "a" not in _fx(tmp_path)


def test_judge_unused_hit_not_counted(tmp_path: Path) -> None:
    # §1: judged evidence requires used=true.
    _note(tmp_path, "a")
    _seed_kind(tmp_path, "u1")
    _judge(tmp_path, "u1", (_hit("a", "helpful", used=False),))
    assert "a" not in _fx(tmp_path)


def test_unknown_outcome_neither_helpful_nor_harmful(tmp_path: Path) -> None:
    _note(tmp_path, "a")
    _seed_kind(tmp_path, "u1")
    _read(tmp_path, "u1", "a", read_id="r1")
    _outcome(tmp_path, "u1", "a", "unknown", read_id="r1")
    fx = _fx(tmp_path)
    assert fx["a"].helpful_refs == 0
    assert fx["a"].harmful_refs == 0
    assert fx["a"].refs == 1  # read still counts as coverage


def test_judgement_helpful_without_read_still_evidence(tmp_path: Path) -> None:
    # judged evidence is independent of an access-log read: a session the
    # judge marks helpful counts even if no read record survived.
    _note(tmp_path, "a")
    _seed_kind(tmp_path, "u1")
    _judge(tmp_path, "u1", (_hit("a", "helpful"),))
    fx = _fx(tmp_path)
    assert fx["a"].helpful_refs == 1
    assert fx["a"].read_session_count == 0  # but no read coverage
    assert fx["a"].distinct_days == 0
