"""TidyPlan 的快照、应用、失败保护与回滚。"""

from __future__ import annotations

from pathlib import Path

import pytest

from trowel_py.memory.store import MemoryStore
from trowel_py.memory.tidy import (
    TidyOperation,
    _tidy_lock,
    apply_plan,
    rollback_plan,
)

from .support import make_plan, write_note


def test_apply_plan_same_id_rerun_does_not_collide(tmp_path: Path) -> None:
    write_note(tmp_path, "mid-a", "A")
    plan = make_plan(
        (TidyOperation(type="retire", target="mid-a", reason="old"),),
        tmp_path,
        plan_id="weekly-2026-W28",
    )
    apply_plan(tmp_path, plan)
    rerun = make_plan(
        (TidyOperation(type="keep", target="mid-a", reason="keep"),),
        tmp_path,
        plan_id="weekly-2026-W28",
    )
    apply_plan(tmp_path, rerun)


def test_tidy_lock_creates_lock_file(tmp_path: Path) -> None:
    with _tidy_lock(tmp_path):
        assert (tmp_path / "meta" / ".tidy.lock").exists()


def test_apply_retire_sets_status(tmp_path: Path) -> None:
    write_note(tmp_path, "mid-a", "A")
    plan = make_plan(
        (TidyOperation(type="retire", target="mid-a", reason="stale"),),
        tmp_path,
    )
    apply_plan(tmp_path, plan)
    notes = {note.memory_id: note for note in MemoryStore(tmp_path).load_notes()}
    assert notes["mid-a"].status == "retired"


def test_apply_supersede_threads_chain(tmp_path: Path) -> None:
    write_note(tmp_path, "mid-old", "Old")
    write_note(tmp_path, "mid-new", "New")
    plan = make_plan(
        (
            TidyOperation(
                type="supersede",
                target="mid-old",
                by="mid-new",
                reason="new corrects old",
            ),
        ),
        tmp_path,
    )
    apply_plan(tmp_path, plan)
    notes = {note.memory_id: note for note in MemoryStore(tmp_path).load_notes()}
    assert notes["mid-old"].status == "superseded"
    assert notes["mid-old"].superseded_by == "mid-new"
    assert "mid-old" in notes["mid-new"].supersedes


def test_apply_merge_sources_absorbs_sources(tmp_path: Path) -> None:
    write_note(tmp_path, "mid-a", "A", sources=("s1", "s2"))
    write_note(tmp_path, "mid-b", "B", sources=("s3",))
    plan = make_plan(
        (
            TidyOperation(
                type="merge_sources",
                target="mid-a",
                canonical="mid-b",
                reason="same topic",
            ),
        ),
        tmp_path,
    )
    apply_plan(tmp_path, plan)
    notes = {note.memory_id: note for note in MemoryStore(tmp_path).load_notes()}
    assert notes["mid-a"].status == "superseded"
    assert notes["mid-a"].superseded_by == "mid-b"
    assert set(notes["mid-b"].sources) >= {"s1", "s2", "s3"}


def test_apply_contradict_sets_status(tmp_path: Path) -> None:
    write_note(tmp_path, "mid-a", "A")
    write_note(tmp_path, "mid-b", "B")
    plan = make_plan(
        (
            TidyOperation(
                type="contradict",
                target="mid-a",
                by="mid-b",
                reason="a is wrong",
            ),
        ),
        tmp_path,
    )
    apply_plan(tmp_path, plan)
    notes = {note.memory_id: note for note in MemoryStore(tmp_path).load_notes()}
    assert notes["mid-a"].status == "contradicted"
    assert notes["mid-a"].superseded_by == "mid-b"


def test_apply_revise_updates_fields(tmp_path: Path) -> None:
    write_note(tmp_path, "mid-a", "A")
    plan = make_plan(
        (
            TidyOperation(
                type="revise",
                target="mid-a",
                reason="narrow scope",
                new_fields={"trigger": "when X", "do_not_use_when": "if Y"},
            ),
        ),
        tmp_path,
    )
    apply_plan(tmp_path, plan)
    [note] = MemoryStore(tmp_path).load_notes()
    assert note.trigger == "when X"
    assert note.do_not_use_when == "if Y"


def test_apply_keep_is_noop(tmp_path: Path) -> None:
    write_note(tmp_path, "mid-a", "A", content_hash="h1")
    plan = make_plan(
        (TidyOperation(type="keep", target="mid-a", reason="fine"),),
        tmp_path,
    )
    apply_plan(tmp_path, plan)
    [note] = MemoryStore(tmp_path).load_notes()
    assert note.status == "active"
    assert note.content_hash == "h1"


def test_apply_rejects_stale_revision(tmp_path: Path) -> None:
    stem = write_note(tmp_path, "mid-a", "A", content_hash="h1")
    plan = make_plan(
        (
            TidyOperation(
                type="retire",
                target="mid-a",
                reason="x",
                expected_revision="h1",
            ),
        ),
        tmp_path,
    )
    MemoryStore(tmp_path).update_note_fields(stem, {"content_hash": "h2"})
    with pytest.raises(ValueError, match="stale"):
        apply_plan(tmp_path, plan)


def test_apply_writes_snapshot_and_report(tmp_path: Path) -> None:
    write_note(tmp_path, "mid-a", "A")
    plan = make_plan(
        (TidyOperation(type="retire", target="mid-a", reason="x"),),
        tmp_path,
    )
    apply_plan(tmp_path, plan)
    snapshot_dir = tmp_path / "meta" / "snapshots" / plan.plan_id
    assert snapshot_dir.exists()
    assert (snapshot_dir / "notes").exists()
    assert (snapshot_dir / "plan.json").exists()
    assert (snapshot_dir / "report.json").exists()


def test_rollback_restores_notes(tmp_path: Path) -> None:
    write_note(tmp_path, "mid-a", "A")
    plan = make_plan(
        (TidyOperation(type="retire", target="mid-a", reason="x"),),
        tmp_path,
    )
    apply_plan(tmp_path, plan)
    notes = {note.memory_id: note for note in MemoryStore(tmp_path).load_notes()}
    assert notes["mid-a"].status == "retired"
    rollback_plan(tmp_path, plan.plan_id)
    notes = {note.memory_id: note for note in MemoryStore(tmp_path).load_notes()}
    assert notes["mid-a"].status == "active"
