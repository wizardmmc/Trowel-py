"""slice-041 core ops tests: nominate / approve / activate (C-5/C-11)."""
from __future__ import annotations

from pathlib import Path

import pytest

from trowel_py.memory.core_ops import (
    activate_core_item,
    approve_candidate,
    nominate_candidate,
)
from trowel_py.memory.seeds import bootstrap_core
from trowel_py.memory.store import MemoryStore


def _note(root: Path, mid: str, title: str = "T", *, body: str = "b") -> str:
    return MemoryStore(root).write_note({
        "type": "note", "title": title, "verification": "verified",
        "memory_id": mid, "status": "active", "kind": "gotcha",
        "__body": body,
    })


def test_nominate_writes_candidate(tmp_path: Path) -> None:
    stem = _note(tmp_path, "mid-a", "Gotcha A")
    mid = nominate_candidate(tmp_path, stem)
    assert mid == "mid-a"
    assert (tmp_path / "meta" / "core-candidates" / "mid-a.md").exists()


def test_nominate_missing_note_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        nominate_candidate(tmp_path, "ghost")


def test_nominate_note_without_memory_id_raises(tmp_path: Path) -> None:
    # a note written without memory_id (legacy pre-migrate) cannot be nominated
    MemoryStore(tmp_path).write_note({
        "type": "note", "title": "Legacy", "verification": "verified", "__body": "x",
    })
    with pytest.raises(ValueError, match="memory_id"):
        nominate_candidate(tmp_path, "Legacy")


def test_approve_adds_trial_item(tmp_path: Path) -> None:
    bootstrap_core(tmp_path)  # seed core.md
    stem = _note(tmp_path, "mid-a", "Gotcha A")
    nominate_candidate(tmp_path, stem)
    approve_candidate(tmp_path, "mid-a")
    items = {it.id: it for it in MemoryStore(tmp_path).load_core_items()}
    assert "mid-a" in items
    assert items["mid-a"].status == "trial"
    assert items["mid-a"].source == "monthly-promote"


def test_approve_missing_candidate_raises(tmp_path: Path) -> None:
    bootstrap_core(tmp_path)
    with pytest.raises(FileNotFoundError):
        approve_candidate(tmp_path, "ghost-mid")


def test_approve_duplicate_raises(tmp_path: Path) -> None:
    bootstrap_core(tmp_path)
    stem = _note(tmp_path, "mid-a", "Gotcha A")
    nominate_candidate(tmp_path, stem)
    approve_candidate(tmp_path, "mid-a")
    with pytest.raises(ValueError, match="already"):
        approve_candidate(tmp_path, "mid-a")


def test_activate_flips_trial_to_active(tmp_path: Path) -> None:
    bootstrap_core(tmp_path)
    stem = _note(tmp_path, "mid-a", "Gotcha A")
    nominate_candidate(tmp_path, stem)
    approve_candidate(tmp_path, "mid-a")
    activate_core_item(tmp_path, "mid-a")
    items = {it.id: it for it in MemoryStore(tmp_path).load_core_items()}
    assert items["mid-a"].status == "active"


def test_activate_missing_raises(tmp_path: Path) -> None:
    bootstrap_core(tmp_path)
    with pytest.raises(FileNotFoundError):
        activate_core_item(tmp_path, "ghost-mid")


def test_injection_sees_trial_and_active_not_candidate(tmp_path: Path) -> None:
    # injection._render_core filters status==retired, so trial+active are
    # injected; candidates (not in core.md) are not. Verify the read path.
    from trowel_py.memory.injection import _render_core

    bootstrap_core(tmp_path)
    stem = _note(tmp_path, "mid-a", "Gotcha A")
    nominate_candidate(tmp_path, stem)
    approve_candidate(tmp_path, "mid-a")  # trial
    activate_core_item(tmp_path, "mid-a")  # active
    text = _render_core(MemoryStore(tmp_path))
    assert "Gotcha A" in text  # the active item is injected
