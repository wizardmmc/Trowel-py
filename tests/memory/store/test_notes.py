"""Knowledge notes 的读写、兼容与并发更新。"""

from pathlib import Path

import pytest

from trowel_py.memory.store import MemoryStore

from .support import _valid_note


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

    assert (tmp_path / "notes" / f"{nid}.md").exists()


def test_write_note_invalid_rejected(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path)
    with pytest.raises(ValueError):
        store.write_note({"type": "note", "title": "x"})


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
    store = MemoryStore(tmp_path)
    store.write_note(_valid_note(retired=True))
    assert len(store.load_notes()) == 1

    assert len(store.load_notes(filter={"retired": False})) == 0
    assert len(store.load_notes(filter={"retired": True})) == 1


def test_filter_by_tag(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path)
    store.write_note(_valid_note(title="A", tags=["frontend"]))
    store.write_note(_valid_note(title="B", tags=["backend"]))
    assert len(store.load_notes(filter={"tag": "frontend"})) == 1


def test_write_note_preserves_unknown_wiki_fields(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path)
    entry = _valid_note()
    entry["sources"] = ["raw/2026-07-08-foo.md"]
    entry["related"] = ["Browser-Cache"]
    store.write_note(entry)
    text = (tmp_path / "notes").glob("*.md").__next__().read_text(encoding="utf-8")
    assert "sources" in text and "related" in text


def test_record_ref_preserves_unknown_fields_and_body(tmp_path: Path) -> None:
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
    import logging

    store = MemoryStore(tmp_path)
    store.write_note(_valid_note())
    (tmp_path / "notes" / "broken.md").write_text(
        "no frontmatter here at all\n", encoding="utf-8"
    )
    with caplog.at_level(logging.WARNING):
        notes = store.load_notes()
    assert len(notes) == 1
    assert any("broken.md" in r.message for r in caplog.records)


def test_load_notes_skips_non_note_type(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path)
    store.write_note(_valid_note())

    (tmp_path / "notes" / "stray.md").write_text(
        "---\ntype: diary\ndate: '2026-07-08'\nlayer: day\n---\nx\n",
        encoding="utf-8",
    )
    assert len(store.load_notes()) == 1


def test_write_note_body_round_trip(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path)
    store.write_note({**_valid_note(), "__body": "根因实测：生成期=0s。"})
    [n] = store.load_notes()
    assert n.body == "根因实测：生成期=0s。"

    text = next((tmp_path / "notes").glob("*.md")).read_text(encoding="utf-8")
    assert "__body" not in text
    assert "根因实测：生成期=0s。" in text


def test_note_from_fm_preserves_body(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path)
    notes_dir = tmp_path / "notes"
    notes_dir.mkdir()
    (notes_dir / "hand.md").write_text(
        "---\n"
        "type: note\ntitle: x\nverification: verified\n"
        "tags: []\nsummary: ''\nconfidence: draft\n"
        "created: ''\nupdated: ''\nrefs: 0\nlast_ref: ''\nretired: false\npain: 0\n"
        "---\nHAND_BODY_MARKER 正文。\n",
        encoding="utf-8",
    )
    [n] = store.load_notes()
    assert "HAND_BODY_MARKER" in n.body


def test_write_note_kind_round_trip(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path)
    store.write_note(_valid_note(kind="procedure"))
    [n] = store.load_notes()
    assert n.kind == "procedure"


def test_load_old_note_without_kind_defaults_fact(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path)
    notes_dir = tmp_path / "notes"
    notes_dir.mkdir()
    (notes_dir / "old.md").write_text(
        "---\n"
        "type: note\ntitle: 老笔记\nverification: verified\n"
        "tags: []\nsummary: ''\nconfidence: evolving\n"
        "refs: 0\nlast_ref: ''\nretired: false\npain: 0\n"
        "---\n老正文。\n",
        encoding="utf-8",
    )
    [n] = store.load_notes()
    assert n.kind == "fact"


def test_write_note_provenance_fields_round_trip(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path)
    store.write_note(
        _valid_note(
            kind="gotcha",
            verification_reason="根因 spike 实测",
            pain_reason="不可逆覆盖",
            conflicts_with=["existing-note-id"],
            source_sessions=["abc-123", "def-456"],
            content_hash="deadbeef",
        )
    )
    [n] = store.load_notes()
    assert n.kind == "gotcha"
    assert n.verification_reason == "根因 spike 实测"
    assert n.pain_reason == "不可逆覆盖"
    assert n.conflicts_with == ("existing-note-id",)
    assert n.source_sessions == ("abc-123", "def-456")
    assert n.content_hash == "deadbeef"
