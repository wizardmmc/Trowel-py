"""tests for persist_draft (slice-040 T9)."""
from __future__ import annotations

from pathlib import Path

from trowel_py.memory.draft import Draft, DraftDiary, DraftNote
from trowel_py.memory.persist import persist_draft
from trowel_py.memory.store import MemoryStore


def _draft(**over) -> Draft:
    base = Draft(
        notes=(DraftNote(title="浏览器缓存", verification="event-data-supported", pain=3, body="build 没生效先查缓存"),),
        diary=(DraftDiary(date="2026-07-09", events="卡两小时在浏览器缓存"),),
    )
    return base


def test_persist_writes_notes_and_diary(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path)
    report = persist_draft(store, _draft(), "2026-07-09")
    assert report.notes_written == 1
    assert report.diary_written == 1
    assert len(store.load_notes()) == 1
    assert len(store.load_diary()) == 1


def test_persist_verification_field_written_consistently(tmp_path: Path) -> None:
    # C-2: draft verification → write_note → load_notes round-trips intact.
    store = MemoryStore(tmp_path)
    draft = Draft(
        notes=(DraftNote(title="错结论", verification="inferred-untested", body="x"),)
    )
    persist_draft(store, draft, "2026-07-09")
    [n] = store.load_notes()
    assert n.verification == "inferred-untested"


def test_persist_inferred_untested_never_stable(tmp_path: Path) -> None:
    # C-2 hard rule: inferred-untested must never land as stable confidence.
    store = MemoryStore(tmp_path)
    draft = Draft(
        notes=(DraftNote(title="x", verification="inferred-untested"),)
    )
    persist_draft(store, draft, "2026-07-09")
    [n] = store.load_notes()
    assert n.confidence != "stable"


def test_persist_never_writes_core(tmp_path: Path) -> None:
    # C-4: persist must never create/modify core.md (layer one is human-only).
    store = MemoryStore(tmp_path)
    assert not (tmp_path / "core.md").exists()
    persist_draft(store, _draft(), "2026-07-09")
    assert not (tmp_path / "core.md").exists()
    assert store.load_core() == ""


def test_persist_diary_lands_under_daily(tmp_path: Path) -> None:
    # C-1: diary lands under diary/daily/ (layer day → dir daily).
    store = MemoryStore(tmp_path)
    persist_draft(store, _draft(), "2026-07-09")
    assert (tmp_path / "diary" / "daily" / "2026-07-09.md").exists()


def test_persist_verification_counts(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path)
    draft = Draft(
        notes=(
            DraftNote(title="a", verification="verified"),
            DraftNote(title="b", verification="inferred-untested"),
            DraftNote(title="c", verification="inferred-untested"),
        )
    )
    report = persist_draft(store, draft, "2026-07-09")
    assert report.verification_counts == {
        "verified": 1,
        "inferred-untested": 2,
    }
