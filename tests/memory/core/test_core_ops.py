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


def _note(
    root: Path,
    mid: str,
    title: str = "T",
    *,
    body: str = "b",
    status: str = "active",
) -> str:
    return MemoryStore(root).write_note(
        {
            "type": "note",
            "title": title,
            "verification": "verified",
            "memory_id": mid,
            "status": status,
            "kind": "gotcha",
            "__body": body,
        }
    )


def test_nominate_writes_candidate(tmp_path: Path) -> None:
    stem = _note(tmp_path, "mid-a", "Gotcha A")
    mid = nominate_candidate(tmp_path, stem)
    assert mid == "mid-a"
    assert (tmp_path / "meta" / "core-candidates" / "mid-a.md").exists()


def test_nominate_missing_note_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        nominate_candidate(tmp_path, "ghost")


def test_nominate_note_without_memory_id_raises(tmp_path: Path) -> None:
    MemoryStore(tmp_path).write_note(
        {
            "type": "note",
            "title": "Legacy",
            "verification": "verified",
            "__body": "x",
        }
    )
    with pytest.raises(ValueError, match="memory_id"):
        nominate_candidate(tmp_path, "Legacy")


def test_nominate_rejects_unsafe_memory_id(tmp_path: Path) -> None:
    stem = _note(tmp_path, "../../core")
    with pytest.raises(ValueError, match="unsafe"):
        nominate_candidate(tmp_path, stem)


def test_approve_adds_trial_item(tmp_path: Path) -> None:
    bootstrap_core(tmp_path)
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


def test_approve_rejects_inactive_note(tmp_path: Path) -> None:
    bootstrap_core(tmp_path)
    stem = _note(tmp_path, "mid-a", status="retired")
    nominate_candidate(tmp_path, stem)
    with pytest.raises(ValueError, match="not active"):
        approve_candidate(tmp_path, "mid-a")


def test_approve_uses_candidate_title_when_note_is_missing(tmp_path: Path) -> None:
    bootstrap_core(tmp_path)
    candidate_dir = tmp_path / "meta" / "core-candidates"
    candidate_dir.mkdir(parents=True)
    (candidate_dir / "orphan.md").write_text(
        "---\nstatus: candidate\nsource_title: Orphan title\n---\nbody\n",
        encoding="utf-8",
    )

    approve_candidate(tmp_path, "orphan")

    items = {item.id: item for item in MemoryStore(tmp_path).load_core_items()}
    assert items["orphan"].imperative == "Orphan title"
    assert items["orphan"].status == "trial"


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
    from trowel_py.memory.injection import _render_core

    bootstrap_core(tmp_path)
    stem = _note(tmp_path, "mid-a", "Gotcha A")
    nominate_candidate(tmp_path, stem)
    store = MemoryStore(tmp_path)
    assert "Gotcha A" not in _render_core(store)

    approve_candidate(tmp_path, "mid-a")
    assert "Gotcha A" in _render_core(store)

    activate_core_item(tmp_path, "mid-a")
    assert "Gotcha A" in _render_core(store)


def test_approve_rejects_blocked_candidate(tmp_path: Path) -> None:
    candidate_dir = tmp_path / "meta" / "core-candidates"
    candidate_dir.mkdir(parents=True)
    (candidate_dir / "mid-x.md").write_text(
        "---\nstatus: blocked\nmemory_id: mid-x\n---\nbody\n", encoding="utf-8"
    )
    with pytest.raises(ValueError, match="blocked"):
        approve_candidate(tmp_path, "mid-x")


def test_approve_rejects_unsafe_candidate_id(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="unsafe"):
        approve_candidate(tmp_path, "../../core")
