"""tests for the file-backed memory store (slice-038 T3)."""
from __future__ import annotations

from pathlib import Path

import pytest

from trowel_py.memory.store import MemoryStore


def _valid_note(**over) -> dict:
    base = {
        "type": "note",
        "title": "浏览器缓存导致 build 不生效",
        "tags": ["frontend", "build"],
        "summary": "build 没生效多半是浏览器缓存",
        "confidence": "evolving",
        "verification": "event-data-supported",
        "refs": 0,
        "last_ref": "",
        "retired": False,
        "pain": 2,
        "created": "2026-07-08",
        "updated": "2026-07-08",
    }
    base.update(over)
    return base


def test_write_note_round_trip(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path)
    nid = store.write_note(_valid_note())
    notes = store.load_notes()
    assert len(notes) == 1
    n = notes[0]
    assert n.title == "浏览器缓存导致 build 不生效"
    assert n.tags == ("frontend", "build")
    assert n.verification == "event-data-supported"
    assert n.refs == 0
    # note id is a stable filename stem, file lives under notes/
    assert (tmp_path / "notes" / f"{nid}.md").exists()


def test_write_note_invalid_rejected(tmp_path: Path) -> None:
    # C-2/C-3: schema-invalid entry must be rejected at write time.
    store = MemoryStore(tmp_path)
    with pytest.raises(ValueError):
        store.write_note({"type": "note", "title": "x"})  # missing verification


def test_record_ref_increments(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path)
    nid = store.write_note(_valid_note())
    store.record_ref(nid, "2026-07-08")
    store.record_ref(nid, "2026-07-09")
    [n] = store.load_notes()
    assert n.refs == 2
    assert n.last_ref == "2026-07-09"


def test_record_ref_unknown_note_raises(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path)
    with pytest.raises(FileNotFoundError):
        store.record_ref("ghost-note", "2026-07-08")


def test_retired_note_still_loaded(tmp_path: Path) -> None:
    # C-4: retirement removes from the default inject set, NOT from load.
    store = MemoryStore(tmp_path)
    store.write_note(_valid_note(retired=True))
    assert len(store.load_notes()) == 1
    # the inject set (039) filters retired out:
    assert len(store.load_notes(filter={"retired": False})) == 0
    assert len(store.load_notes(filter={"retired": True})) == 1


def test_filter_by_tag(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path)
    store.write_note(_valid_note(title="A", tags=["frontend"]))
    store.write_note(_valid_note(title="B", tags=["backend"]))
    assert len(store.load_notes(filter={"tag": "frontend"})) == 1


def test_write_note_preserves_unknown_wiki_fields(tmp_path: Path) -> None:
    # W3: wiki-compatible fields beyond the known set (sources/related) survive
    # write_note, honoring "reuse the wiki-compatible subset" on migration.
    store = MemoryStore(tmp_path)
    entry = _valid_note()
    entry["sources"] = ["raw/2026-07-08-foo.md"]
    entry["related"] = ["Browser-Cache"]
    store.write_note(entry)
    text = (tmp_path / "notes").glob("*.md").__next__().read_text(encoding="utf-8")
    assert "sources" in text and "related" in text


def test_record_ref_preserves_unknown_fields_and_body(tmp_path: Path) -> None:
    # W3 symmetry: record_ref must not drop unknown fields, and must keep body.
    store = MemoryStore(tmp_path)
    nid = store.write_note({**_valid_note(), "sources": ["raw/x.md"]})
    note_path = tmp_path / "notes" / f"{nid}.md"
    note_path.write_text(
        note_path.read_text(encoding="utf-8") + "正文 body 必须保留。\n",
        encoding="utf-8",
    )
    store.record_ref(nid, "2026-07-09")
    text = note_path.read_text(encoding="utf-8")
    assert "sources" in text
    assert "正文 body 必须保留" in text
    [n] = store.load_notes()
    assert n.refs == 1 and n.last_ref == "2026-07-09"


def test_load_notes_warns_on_corrupt_frontmatter(tmp_path: Path, caplog) -> None:
    # W4: a hand-corrupted note is skipped with a warning, not silently dropped.
    import logging
    store = MemoryStore(tmp_path)
    store.write_note(_valid_note())
    (tmp_path / "notes" / "broken.md").write_text(
        "no frontmatter here at all\n", encoding="utf-8"
    )
    with caplog.at_level(logging.WARNING):
        notes = store.load_notes()
    assert len(notes) == 1  # the good one survives
    assert any("broken.md" in r.message for r in caplog.records)


def test_load_notes_skips_non_note_type(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path)
    store.write_note(_valid_note())
    # a stray non-note .md in notes/ (type mismatch) is skipped, not crashed
    (tmp_path / "notes" / "stray.md").write_text(
        "---\ntype: diary\ndate: '2026-07-08'\nlayer: day\n---\nx\n",
        encoding="utf-8",
    )
    assert len(store.load_notes()) == 1


def test_dual_track_separation(tmp_path: Path) -> None:
    # C-1: knowledge notes live under notes/, diary under diary/ — never mixed.
    store = MemoryStore(tmp_path)
    store.write_note(_valid_note())
    # a diary file placed by the (future) write loop:
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
    # the note file is not under diary/ and vice versa:
    assert not (tmp_path / "diary" / "浏览器缓存导致 build 不生效.md").exists()


def test_load_diary_filters(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path)
    day_dir = tmp_path / "diary" / "daily"
    week_dir = tmp_path / "diary" / "weekly"
    day_dir.mkdir(parents=True)
    week_dir.mkdir(parents=True)
    (day_dir / "2026-07-08.md").write_text(_diary_text("2026-07-08", "day"), encoding="utf-8")
    (week_dir / "2026-W28.md").write_text(_diary_text("2026-07-08", "week"), encoding="utf-8")
    assert len(store.load_diary()) == 2
    assert len(store.load_diary(layer="day")) == 1
    assert len(store.load_diary(layer="week")) == 1


def test_load_dictionary_L0_absent_returns_empty(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path)
    assert store.load_dictionary_L0() == ""


def _diary_text(date: str, layer: str) -> str:
    return (
        "---\n"
        f"type: diary\ndate: '{date}'\nlayer: {layer}\n"
        f"period: '{date}'\npromoted_knowledge: []\n"
        "---\nbody\n"
    )


def test_load_core_items(tmp_path: Path) -> None:
    # slice-039: parse core.md frontmatter items into CoreItem (all, incl retired).
    from trowel_py.memory.seeds import bootstrap_core

    bootstrap_core(tmp_path)
    store = MemoryStore(tmp_path)
    items = store.load_core_items()
    assert len(items) == 8  # the 8 seed imperatives
    assert items[0].id == "lookup-first"
    assert all(it.status == "seed" for it in items)


def test_load_core_items_absent(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path)
    assert store.load_core_items() == ()


def test_load_diary_carries_body(tmp_path: Path) -> None:
    # slice-039: diary injection needs the event-stream body, not just frontmatter.
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
