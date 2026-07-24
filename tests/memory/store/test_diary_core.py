"""Diary、core 与 dictionary 根索引。"""

from pathlib import Path

import pytest

from trowel_py.memory.store import MemoryStore

from .support import _diary_text, _valid_note


def test_dual_track_separation(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path)
    store.write_note(_valid_note())

    (tmp_path / "diary" / "daily").mkdir(parents=True)
    (tmp_path / "diary" / "daily" / "2026-07-08.md").write_text(
        "---\n"
        "type: diary\n"
        "date: '2026-07-08'\n"
        "layer: day\n"
        "period: '2026-07-08'\n"
        "promoted_knowledge: []\n"
        "---\n"
        "卡在浏览器缓存两小时。\n",
        encoding="utf-8",
    )
    notes = store.load_notes()
    diaries = store.load_diary()
    assert len(notes) == 1 and notes[0].title.startswith("浏览器")
    assert len(diaries) == 1 and diaries[0].layer == "day"

    assert not (tmp_path / "diary" / "浏览器缓存导致 build 不生效.md").exists()


def test_load_diary_filters(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path)
    day_dir = tmp_path / "diary" / "daily"
    week_dir = tmp_path / "diary" / "weekly"
    day_dir.mkdir(parents=True)
    week_dir.mkdir(parents=True)
    (day_dir / "2026-07-08.md").write_text(
        _diary_text("2026-07-08", "day"), encoding="utf-8"
    )
    (week_dir / "2026-W28.md").write_text(
        _diary_text("2026-07-08", "week"), encoding="utf-8"
    )
    assert len(store.load_diary()) == 2
    assert len(store.load_diary(layer="day")) == 1
    assert len(store.load_diary(layer="week")) == 1


def test_load_dictionary_L0_absent_returns_empty(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path)
    assert store.load_dictionary_L0() == ""


def test_load_core_items(tmp_path: Path) -> None:
    from trowel_py.memory.seeds import bootstrap_core

    bootstrap_core(tmp_path)
    store = MemoryStore(tmp_path)
    items = store.load_core_items()
    assert len(items) == 8
    assert items[0].id == "lookup-first"
    assert all(it.status == "seed" for it in items)


def test_load_core_items_absent(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path)
    assert store.load_core_items() == ()


def test_load_diary_carries_body(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path)
    day_dir = tmp_path / "diary" / "daily"
    day_dir.mkdir(parents=True)
    (day_dir / "2026-07-08.md").write_text(
        "---\ntype: diary\ndate: '2026-07-08'\nlayer: day\n"
        "period: '2026-07-08'\npromoted_knowledge: []\n"
        "---\nDIARY_BODY_MARKER 事件流正文。\n",
        encoding="utf-8",
    )
    [d] = store.load_diary()
    assert "DIARY_BODY_MARKER" in d.body


def test_write_diary_creates_file_under_diary_daily(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path)
    did = store.write_diary(
        {
            "type": "diary",
            "date": "2026-07-09",
            "layer": "day",
            "period": "2026-07-09",
            "promoted_knowledge": [],
            "__body": "卡两小时在浏览器缓存。",
        }
    )
    assert did == "2026-07-09"
    assert (tmp_path / "diary" / "daily" / "2026-07-09.md").exists()
    [d] = store.load_diary()
    assert d.date == "2026-07-09"
    assert d.layer == "day"
    assert "卡两小时" in d.body


def test_write_diary_invalid_rejected(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path)
    with pytest.raises(ValueError):
        store.write_diary({"type": "diary", "layer": "day"})
    with pytest.raises(ValueError):
        store.write_diary({"type": "diary", "date": "2026-07-09", "layer": "bogus"})


def test_write_diary_and_note_physically_separate(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path)
    store.write_note(_valid_note())
    store.write_diary(
        {
            "type": "diary",
            "date": "2026-07-09",
            "layer": "day",
            "__body": "事件流。",
        }
    )
    note_files = list((tmp_path / "notes").glob("*.md"))
    diary_files = list((tmp_path / "diary").rglob("*.md"))
    assert len(note_files) == 1
    assert len(diary_files) == 1

    assert note_files[0].relative_to(tmp_path).parts[0] == "notes"
    assert diary_files[0].relative_to(tmp_path).parts[0] == "diary"
