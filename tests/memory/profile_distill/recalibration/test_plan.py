from pathlib import Path

import pytest

from trowel_py.memory.profile_recalibrate import (
    RecalibrationScopeError,
    plan_recalibration,
)

from .support import live_hashes, seed_live_files, seed_session


def test_plan_requires_explicit_scope(tmp_path: Path) -> None:
    root = tmp_path / "memory"
    root.mkdir()
    with pytest.raises(RecalibrationScopeError):
        plan_recalibration(root, scope_all=False, from_date=None)


def test_plan_rejects_both_scope_modes(tmp_path: Path) -> None:
    root = tmp_path / "memory"
    root.mkdir()
    with pytest.raises(RecalibrationScopeError):
        plan_recalibration(root, scope_all=True, from_date="2026-07-01")


def test_plan_from_date_filters_sessions(tmp_path: Path) -> None:
    root = tmp_path / "memory"
    root.mkdir()
    seed_session(
        root, "old", date="2026-06-01", registered_at="2026-06-01T10:00:00"
    )
    seed_session(
        root, "new", date="2026-07-10", registered_at="2026-07-10T10:00:00"
    )
    plan = plan_recalibration(root, scope_all=False, from_date="2026-07-01")
    assert [session.cc_session_id for session in plan.sessions] == ["new"]


def test_plan_does_not_create_staging(tmp_path: Path) -> None:
    root = tmp_path / "memory"
    root.mkdir()
    plan_recalibration(root, scope_all=True)
    assert not (root / "meta" / "profile-recalibration").exists()


def test_plan_on_fresh_root_does_not_create_sessions_db(tmp_path: Path) -> None:
    root = tmp_path / "memory"
    root.mkdir()
    plan = plan_recalibration(root, scope_all=True)
    assert plan.sessions == ()
    assert plan.estimated_agent_calls == 0
    assert not (root / "meta" / "sessions.db").exists()
    assert not (root / "meta").exists()


def test_plan_leaves_live_files_byte_identical(tmp_path: Path) -> None:
    root = tmp_path / "memory"
    root.mkdir()
    seed_live_files(root)
    jsonl = tmp_path / "s1.jsonl"
    jsonl.write_text("payload", encoding="utf-8")
    seed_session(root, "s1", jsonl_path=str(jsonl))
    before = live_hashes(root, [jsonl])
    plan_recalibration(root, scope_all=True)
    assert live_hashes(root, [jsonl]) == before


def test_plan_freezes_user_sessions_and_offsets(tmp_path: Path) -> None:
    root = tmp_path / "memory"
    root.mkdir()
    jsonl = tmp_path / "s1.jsonl"
    jsonl.write_text("payload", encoding="utf-8")
    seed_session(root, "s1", completed=1234, jsonl_path=str(jsonl))
    plan = plan_recalibration(root, scope_all=True)
    [frozen] = plan.sessions
    assert frozen.cc_session_id == "s1"
    assert frozen.end_offset == 1234
    assert frozen.jsonl_exists is True
    assert plan.estimated_agent_calls == 1


def test_plan_excludes_review_distill_eval_kinds(tmp_path: Path) -> None:
    root = tmp_path / "memory"
    root.mkdir()
    seed_session(root, "user")
    seed_session(root, "rev", kind="review")
    seed_session(root, "dist", kind="distill")
    seed_session(root, "eval", kind="eval")
    plan = plan_recalibration(root, scope_all=True)
    assert [session.cc_session_id for session in plan.sessions] == ["user"]


def test_plan_reports_missing_jsonl(tmp_path: Path) -> None:
    root = tmp_path / "memory"
    root.mkdir()
    seed_session(root, "gone", jsonl_path="/does/not/exist.jsonl")
    plan = plan_recalibration(root, scope_all=True)
    assert "gone" in plan.missing_jsonl
    assert plan.estimated_agent_calls == 0


def test_plan_hashes_live_files_marks_missing(tmp_path: Path) -> None:
    root = tmp_path / "memory"
    root.mkdir()
    (root / "profile.md").write_text("only profile", encoding="utf-8")
    plan = plan_recalibration(root, scope_all=True)
    assert plan.live_hashes.profile is not None
    assert plan.live_hashes.suggestions is None
    assert plan.live_hashes.watermark is None
    assert plan.live_hashes.to_manifest_dict()["watermark"] == "missing"
