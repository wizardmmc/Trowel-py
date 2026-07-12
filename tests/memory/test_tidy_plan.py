"""slice-041 TidyPlan tests: validate + apply + rollback (C-7/C-12).

The LLM only PRODUCES a plan; Python validates the invariants and applies it
atomically with a backup. ``--rollback`` restores from the snapshot.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from trowel_py.memory.store import MemoryStore
from trowel_py.memory.tidy import (
    TidyOperation,
    TidyPlan,
    apply_plan,
    rollback_plan,
    validate_plan,
)


def _note(root: Path, mid: str, title: str, *, sources: tuple[str, ...] = (),
          content_hash: str = "h1") -> str:
    """Write a note with a pinned memory_id; returns the file stem."""
    store = MemoryStore(root)
    return store.write_note({
        "type": "note", "title": title, "verification": "verified",
        "memory_id": mid, "status": "active", "sources": list(sources),
        "content_hash": content_hash, "__body": f"body of {title}",
    })


def _snapshot(root: Path) -> dict[str, str]:
    """memory_id → content_hash map for the current corpus."""
    out: dict[str, str] = {}
    for _stem, n in MemoryStore(root).load_notes_with_id():
        if n.memory_id:
            out[n.memory_id] = n.content_hash
    return out


def _plan(ops: tuple[TidyOperation, ...], root: Path, plan_id: str = "p1") -> TidyPlan:
    return TidyPlan(
        plan_id=plan_id,
        source_snapshot=_snapshot(root),
        operations=ops,
    )


# ---------- validation ----------


def test_validate_rejects_unknown_target(tmp_path: Path) -> None:
    _note(tmp_path, "mid-a", "A")
    plan = _plan((TidyOperation(type="retire", target="mid-ghost", reason="x"),), tmp_path)
    errors = validate_plan(tmp_path, plan)
    assert errors
    assert any("mid-ghost" in e for e in errors)


def test_validate_rejects_supersede_cycle(tmp_path: Path) -> None:
    _note(tmp_path, "mid-a", "A")
    _note(tmp_path, "mid-b", "B")
    plan = _plan((
        TidyOperation(type="supersede", target="mid-a", by="mid-b", reason="b replaces a"),
        TidyOperation(type="supersede", target="mid-b", by="mid-a", reason="a replaces b"),
    ), tmp_path)
    errors = validate_plan(tmp_path, plan)
    assert any("cycle" in e.lower() or "环" in e for e in errors)


def test_validate_rejects_merge_canonical_missing(tmp_path: Path) -> None:
    _note(tmp_path, "mid-a", "A")
    plan = _plan((
        TidyOperation(type="merge_sources", target="mid-a", canonical="mid-ghost",
                      reason="merge"),
    ), tmp_path)
    errors = validate_plan(tmp_path, plan)
    assert any("mid-ghost" in e for e in errors)


def test_validate_accepts_clean_plan(tmp_path: Path) -> None:
    _note(tmp_path, "mid-a", "A")
    _note(tmp_path, "mid-b", "B")
    plan = _plan((
        TidyOperation(type="supersede", target="mid-a", by="mid-b", reason="b replaces a"),
    ), tmp_path)
    assert validate_plan(tmp_path, plan) == []


def test_validate_rejects_cycle_with_existing_chain(tmp_path: Path) -> None:
    """C1 (codex): cycle detection must include persisted superseded_by edges,
    not just the current plan's edges. Existing A->B + new B->A = cycle."""
    _note(tmp_path, "mid-a", "A")
    _note(tmp_path, "mid-b", "B")
    # persist A->B first (A superseded by B)
    first = _plan((
        TidyOperation(type="supersede", target="mid-a", by="mid-b", reason="b replaces a"),
    ), tmp_path)
    apply_plan(tmp_path, first)
    # new plan tries B->A — closes the cycle with the existing persisted edge
    second = _plan((
        TidyOperation(type="supersede", target="mid-b", by="mid-a", reason="a replaces b"),
    ), tmp_path)
    errors = validate_plan(tmp_path, second)
    assert any("cycle" in e.lower() or "环" in e for e in errors)


def test_validate_rejects_self_supersede(tmp_path: Path) -> None:
    """C1 (codex): a note cannot replace itself (target == by/canonical)."""
    _note(tmp_path, "mid-a", "A")
    plan = _plan((
        TidyOperation(type="supersede", target="mid-a", by="mid-a", reason="self"),
    ), tmp_path)
    errors = validate_plan(tmp_path, plan)
    assert any("self" in e.lower() or "自指" in e for e in errors)


# ---------- C2 (codex): revise.new_fields whitelist + schema ----------


def test_validate_rejects_revise_identity_field(tmp_path: Path) -> None:
    """C2: revise cannot touch identity fields (memory_id/type/content_hash/...)."""
    _note(tmp_path, "mid-a", "A")
    plan = _plan((
        TidyOperation(type="revise", target="mid-a", reason="malicious",
                      new_fields={"memory_id": "BROKEN"}),
    ), tmp_path)
    errors = validate_plan(tmp_path, plan)
    assert any("allowlist" in e.lower() or "memory_id" in e for e in errors)


def test_validate_rejects_revise_status(tmp_path: Path) -> None:
    """C2: revise cannot change lifecycle (status) — retire/supersede's job."""
    _note(tmp_path, "mid-a", "A")
    plan = _plan((
        TidyOperation(type="revise", target="mid-a", reason="lifecycle",
                      new_fields={"status": "retired"}),
    ), tmp_path)
    errors = validate_plan(tmp_path, plan)
    assert errors  # status not in allowlist


def test_validate_rejects_revise_bad_enum(tmp_path: Path) -> None:
    """C2: even allowlisted fields must pass schema (verification enum)."""
    _note(tmp_path, "mid-a", "A")
    plan = _plan((
        TidyOperation(type="revise", target="mid-a", reason="bad enum",
                      new_fields={"verification": "not-a-real-value"}),
    ), tmp_path)
    errors = validate_plan(tmp_path, plan)
    assert any("schema" in e.lower() or "verification" in e for e in errors)


def test_validate_accepts_revise_allowlisted(tmp_path: Path) -> None:
    """C2: revise with allowlisted + schema-valid fields passes."""
    _note(tmp_path, "mid-a", "A")
    plan = _plan((
        TidyOperation(type="revise", target="mid-a", reason="scope",
                      new_fields={"trigger": "when X", "verification": "verified"}),
    ), tmp_path)
    assert validate_plan(tmp_path, plan) == []


# ---------- W3 (codex): tidy flock + same-period rerun ----------


def test_apply_plan_same_id_rerun_does_not_collide(tmp_path: Path) -> None:
    """W3: re-running apply with the same plan_id must not raise
    FileExistsError on the snapshot directory (same-period rerun)."""
    _note(tmp_path, "mid-a", "A")
    plan = _plan((
        TidyOperation(type="retire", target="mid-a", reason="old"),
    ), tmp_path, plan_id="weekly-2026-W28")
    apply_plan(tmp_path, plan)
    # rerun same plan_id with a fresh snapshot — previously FileExistsError
    plan2 = _plan((
        TidyOperation(type="keep", target="mid-a", reason="keep"),
    ), tmp_path, plan_id="weekly-2026-W28")
    apply_plan(tmp_path, plan2)  # must not raise


def test_tidy_lock_creates_lock_file(tmp_path: Path) -> None:
    """W3: _tidy_lock materializes the lock file (C-12 mutex exists)."""
    from trowel_py.memory.tidy import _tidy_lock

    with _tidy_lock(tmp_path):
        assert (tmp_path / "meta" / ".tidy.lock").exists()


# ---------- apply: each operation ----------


def test_apply_retire_sets_status(tmp_path: Path) -> None:
    _note(tmp_path, "mid-a", "A")
    plan = _plan((TidyOperation(type="retire", target="mid-a", reason="stale"),), tmp_path)
    apply_plan(tmp_path, plan)
    notes = {n.memory_id: n for n in MemoryStore(tmp_path).load_notes()}
    assert notes["mid-a"].status == "retired"


def test_apply_supersede_threads_chain(tmp_path: Path) -> None:
    # C-7: old → superseded + superseded_by; new gains supersedes=[old]
    _note(tmp_path, "mid-old", "Old")
    _note(tmp_path, "mid-new", "New")
    plan = _plan((
        TidyOperation(type="supersede", target="mid-old", by="mid-new",
                      reason="new corrects old"),
    ), tmp_path)
    apply_plan(tmp_path, plan)
    notes = {n.memory_id: n for n in MemoryStore(tmp_path).load_notes()}
    assert notes["mid-old"].status == "superseded"
    assert notes["mid-old"].superseded_by == "mid-new"
    assert "mid-old" in notes["mid-new"].supersedes


def test_apply_merge_sources_absorbs_sources(tmp_path: Path) -> None:
    _note(tmp_path, "mid-a", "A", sources=("s1", "s2"))
    _note(tmp_path, "mid-b", "B", sources=("s3",))
    plan = _plan((
        TidyOperation(type="merge_sources", target="mid-a", canonical="mid-b",
                      reason="same topic"),
    ), tmp_path)
    apply_plan(tmp_path, plan)
    notes = {n.memory_id: n for n in MemoryStore(tmp_path).load_notes()}
    assert notes["mid-a"].status == "superseded"
    assert notes["mid-a"].superseded_by == "mid-b"
    # canonical absorbed target's sources (no source lost — C-12)
    assert set(notes["mid-b"].sources) >= {"s1", "s2", "s3"}


def test_apply_contradict_sets_status(tmp_path: Path) -> None:
    _note(tmp_path, "mid-a", "A")
    _note(tmp_path, "mid-b", "B")
    plan = _plan((
        TidyOperation(type="contradict", target="mid-a", by="mid-b", reason="a is wrong"),
    ), tmp_path)
    apply_plan(tmp_path, plan)
    notes = {n.memory_id: n for n in MemoryStore(tmp_path).load_notes()}
    assert notes["mid-a"].status == "contradicted"
    assert notes["mid-a"].superseded_by == "mid-b"


def test_apply_revise_updates_fields(tmp_path: Path) -> None:
    _note(tmp_path, "mid-a", "A")
    plan = _plan((
        TidyOperation(type="revise", target="mid-a", reason="narrow scope",
                      new_fields={"trigger": "when X", "do_not_use_when": "if Y"}),
    ), tmp_path)
    apply_plan(tmp_path, plan)
    [n] = MemoryStore(tmp_path).load_notes()
    assert n.trigger == "when X"
    assert n.do_not_use_when == "if Y"


def test_apply_keep_is_noop(tmp_path: Path) -> None:
    _note(tmp_path, "mid-a", "A", content_hash="h1")
    plan = _plan((TidyOperation(type="keep", target="mid-a", reason="fine"),), tmp_path)
    apply_plan(tmp_path, plan)
    [n] = MemoryStore(tmp_path).load_notes()
    assert n.status == "active"
    assert n.content_hash == "h1"


# ---------- stale + atomic + rollback ----------


def test_apply_rejects_stale_revision(tmp_path: Path) -> None:
    # plan built against hash h1, but the note changed to h2 before apply
    stem = _note(tmp_path, "mid-a", "A", content_hash="h1")
    plan = _plan((
        TidyOperation(type="retire", target="mid-a", reason="x",
                      expected_revision="h1"),
    ), tmp_path)
    # mutate the note after the plan was built
    MemoryStore(tmp_path).update_note_fields(stem, {"content_hash": "h2"})
    # rebuild snapshot is stale now; apply must refuse
    with pytest.raises(ValueError, match="stale"):
        apply_plan(tmp_path, plan)


def test_apply_writes_snapshot_and_report(tmp_path: Path) -> None:
    _note(tmp_path, "mid-a", "A")
    plan = _plan((TidyOperation(type="retire", target="mid-a", reason="x"),), tmp_path)
    apply_plan(tmp_path, plan)
    snap = tmp_path / "meta" / "snapshots" / plan.plan_id
    assert snap.exists()
    assert (snap / "notes").exists()  # backup of notes/
    assert (snap / "plan.json").exists()
    assert (snap / "report.json").exists()


def test_rollback_restores_notes(tmp_path: Path) -> None:
    _note(tmp_path, "mid-a", "A")
    plan = _plan((TidyOperation(type="retire", target="mid-a", reason="x"),), tmp_path)
    apply_plan(tmp_path, plan)
    # verify retired
    assert {n.memory_id: n for n in MemoryStore(tmp_path).load_notes()}["mid-a"].status == "retired"
    rollback_plan(tmp_path, plan.plan_id)
    # back to active
    assert {n.memory_id: n for n in MemoryStore(tmp_path).load_notes()}["mid-a"].status == "active"
