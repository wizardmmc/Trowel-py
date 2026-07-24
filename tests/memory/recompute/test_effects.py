"""read、outcome 与 judgement 的会话级效果聚合。"""

from pathlib import Path

from tests.memory.recompute.support import (
    _fx,
    _hit,
    _judge,
    _note,
    _outcome,
    _read,
    _seed_kind,
)
from trowel_py.memory.access_log import (
    AccessRecord,
    OutcomeRecord,
    log_access,
    log_outcome,
)


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
    _note(tmp_path, "a")
    _seed_kind(tmp_path, "u1")
    _judge(tmp_path, "u1", (_hit("a", "helpful"),))
    fx = _fx(tmp_path)
    assert fx["a"].helpful_refs == 1


def test_conflict_helpful_and_harmful_resolves_harmful(tmp_path: Path) -> None:
    _note(tmp_path, "a")
    _seed_kind(tmp_path, "u1")
    _read(tmp_path, "u1", "a", read_id="r1")
    _outcome(tmp_path, "u1", "a", "helpful", read_id="r1")
    _judge(tmp_path, "u1", (_hit("a", "harmful"),))
    fx = _fx(tmp_path)
    assert fx["a"].harmful_refs == 1
    assert fx["a"].helpful_refs == 0


def test_repeat_read_same_session_is_one_session(tmp_path: Path) -> None:
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
    _note(tmp_path, "a")
    _seed_kind(tmp_path, "u1")
    _judge(tmp_path, "u1", (_hit("a", "helpful"),), segment="seg1")
    _judge(tmp_path, "u1", (_hit("a", "helpful"),), segment="seg2")
    fx = _fx(tmp_path)
    assert fx["a"].helpful_refs == 1


def test_repeat_same_segment_idempotent(tmp_path: Path) -> None:
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
    assert fx["a"].distinct_days == 2

    assert fx["a"].last_ref == "2026-07-05"


def test_distinct_days_counts_only_helpful_evidence(tmp_path: Path) -> None:
    _note(tmp_path, "a")
    _seed_kind(tmp_path, "u1")
    _seed_kind(tmp_path, "u2")
    _read(tmp_path, "u1", "a", read_id="r1", ts="2026-07-01T10:00:00+00:00")
    _outcome(tmp_path, "u1", "a", "helpful", read_id="r1")
    _read(tmp_path, "u2", "a", read_id="r2", ts="2026-07-02T10:00:00+00:00")
    fx = _fx(tmp_path)
    assert fx["a"].distinct_days == 1
    assert fx["a"].last_ref == "2026-07-02"


def test_eval_session_excluded_from_everything(tmp_path: Path) -> None:
    _note(tmp_path, "a")
    _seed_kind(tmp_path, "u1")
    _seed_kind(tmp_path, "e1", "eval")
    _read(tmp_path, "u1", "a", read_id="ru")
    _outcome(tmp_path, "u1", "a", "helpful", read_id="ru")
    _read(tmp_path, "e1", "a", read_id="re")
    _outcome(tmp_path, "e1", "a", "helpful", read_id="re")
    _judge(tmp_path, "e1", (_hit("a", "helpful"),))
    fx = _fx(tmp_path)
    assert fx["a"].refs == 1

    assert fx["a"].helpful_refs == 1

    assert fx["a"].read_sessions == frozenset({"u1"})


def test_unattributed_record_excluded(tmp_path: Path) -> None:
    _note(tmp_path, "a")
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


def test_outcome_without_matching_read_dropped(tmp_path: Path) -> None:
    _note(tmp_path, "a")
    _seed_kind(tmp_path, "u1")
    _outcome(tmp_path, "u1", "a", "helpful", read_id="ghost")
    assert "a" not in _fx(tmp_path)


def test_outcome_from_eval_session_quoting_user_read_dropped(tmp_path: Path) -> None:
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
    assert fx["a"].refs == 1


def test_judgement_helpful_without_read_still_evidence(tmp_path: Path) -> None:
    _note(tmp_path, "a")
    _seed_kind(tmp_path, "u1")
    _judge(tmp_path, "u1", (_hit("a", "helpful"),))
    fx = _fx(tmp_path)
    assert fx["a"].helpful_refs == 1
    assert fx["a"].read_session_count == 0

    assert fx["a"].distinct_days == 0


def test_explicit_unused_outcome_counted(tmp_path: Path) -> None:
    _note(tmp_path, "a")
    _seed_kind(tmp_path, "u1")
    _read(tmp_path, "u1", "a", read_id="r1")
    _outcome(tmp_path, "u1", "a", "unused", read_id="r1")

    effects = _fx(tmp_path)

    assert "u1" in effects["a"].unused_sessions
    assert effects["a"].helpful_refs == 0
    assert effects["a"].harmful_refs == 0
