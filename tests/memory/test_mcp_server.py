"""tests for the memory MCP server pure-logic handlers (slice-040-c)."""
from __future__ import annotations

from pathlib import Path

import pytest

from trowel_py.memory.access_log import read_access_log, read_outcome_log
from trowel_py.memory.mcp_server import (
    handle_outcome,
    handle_read,
    handle_search,
    parse_memory_uri,
    requires_read,
)
from trowel_py.memory.store import MemoryStore
from trowel_py.memory.types import Note

IDENT = {"trowel_session_id": "t-1", "cc_session_id": "c-1"}


def _write_note(root: Path, slug: str, **kw) -> None:
    store = MemoryStore(root)
    entry = {
        "type": "note",
        "title": kw.get("title", slug),
        "tags": kw.get("tags", []),
        "summary": kw.get("summary", "s"),
        "confidence": "draft",
        "verification": kw.get("verification", "inferred-untested"),
        "kind": kw.get("kind", "fact"),
        "refs": 0,
        "last_ref": "",
        "retired": kw.get("retired", False),
        "pain": 0,
        "created": "2026-07-11",
        "updated": "2026-07-11",
    }
    store.write_note(entry)
    # write_note slugs the title; rename to the requested slug if different
    pass


class _FakeRetriever:
    def __init__(self, stems: list[str]) -> None:
        self._stems = stems

    def __call__(self, query, *, corpus_dir, dictionary_path):
        return list(self._stems)


# ---- parse_memory_uri (C-7) ----


def test_parse_uri_valid() -> None:
    assert parse_memory_uri("memory://notes/my-note") == "my-note"


@pytest.mark.parametrize("bad", [
    "http://x/y", "memory://notes/", "memory://notes/../etc",
    "memory://notes/a/b", "memory://notes/.hidden",
])
def test_parse_uri_rejects(bad: str) -> None:
    with pytest.raises(ValueError):
        parse_memory_uri(bad)


# ---- requires_read (C-4) ----


def _note(**kw) -> Note:
    return Note(type="note", title="t", kind=kw.get("kind", "fact"),
                verification=kw.get("verification", "verified"))


def test_requires_read_by_kind() -> None:
    assert requires_read(_note(kind="gotcha"))
    assert requires_read(_note(kind="procedure"))
    assert requires_read(_note(kind="hypothesis"))
    assert not requires_read(_note(kind="fact"))


def test_requires_read_by_verification() -> None:
    assert requires_read(_note(kind="fact", verification="inferred-untested"))
    assert not requires_read(_note(kind="fact", verification="verified"))


def test_search_description_requires_read_hint() -> None:
    """slice-052 温和版: the search tool description must tell the model that
    requires_read=true hits can't be summary-scanned — memory.read the body."""
    from trowel_py.memory.mcp_server import _SEARCH_DESC

    assert "requires_read" in _SEARCH_DESC
    assert "memory.read" in _SEARCH_DESC


# ---- handle_search ----


def test_search_dictionary_empty_returns_hint(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path)
    out = handle_search("q", 5, False, store, tmp_path / "dictionary-L0.md", IDENT)
    assert out["results"] == []
    assert out["error"] == "dictionary_empty"
    assert "dict-rebuild" in out["hint"]
    # search still logged
    logs = read_access_log(tmp_path)
    assert any(r.action == "search" and r.query == "q" for r in logs)


def test_search_with_dictionary_returns_hits(tmp_path: Path) -> None:
    (tmp_path / "dictionary-L0.md").write_text("L0", encoding="utf-8")
    store = MemoryStore(tmp_path)
    store.write_note({"type": "note", "title": "Note One", "summary": "first",
                      "kind": "gotcha", "verification": "inferred-untested",
                      "confidence": "draft", "refs": 0, "last_ref": "", "retired": False})
    # slug of "Note One" = "Note-One"
    out = handle_search("note", 5, False, store, tmp_path / "dictionary-L0.md", IDENT,
                        retriever=_FakeRetriever(["Note-One"]))
    assert len(out["results"]) == 1
    hit = out["results"][0]
    assert hit["memory_id"] == "Note-One"
    assert hit["uri"] == "memory://notes/Note-One"
    assert hit["requires_read"] is True  # gotcha + inferred-untested


def test_search_filters_retired(tmp_path: Path) -> None:
    (tmp_path / "dictionary-L0.md").write_text("L0", encoding="utf-8")
    store = MemoryStore(tmp_path)
    store.write_note({"type": "note", "title": "Retired One", "summary": "r",
                      "kind": "fact", "verification": "verified",
                      "confidence": "draft", "refs": 0, "last_ref": "", "retired": True})
    out = handle_search("r", 5, False, store, tmp_path / "dictionary-L0.md", IDENT,
                        retriever=_FakeRetriever(["Retired-One"]))
    assert out["results"] == []  # retired filtered out


# ---- handle_read ----


def test_read_records_ref_and_log(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path)
    store.write_note({"type": "note", "title": "Read Me", "summary": "s",
                      "kind": "fact", "verification": "verified",
                      "confidence": "draft", "refs": 0, "last_ref": "", "retired": False,
                      "__body": "BODY_MARKER"})
    out = handle_read("memory://notes/Read-Me", "search-1", store, IDENT)
    assert out["body"] == "BODY_MARKER"
    assert "read_id" in out
    # refs incremented
    note = store.load_note("Read-Me")
    assert note.refs == 1
    # access-log has read action
    logs = read_access_log(tmp_path)
    assert any(r.action == "read" and r.memory_id == "Read-Me" for r in logs)


def test_read_unknown_returns_not_found(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path)
    out = handle_read("memory://notes/nope", "", store, IDENT)
    assert out["error"] == "not_found"


def test_read_illegal_uri_raises(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path)
    out = handle_read("memory://notes/../etc/passwd", "", store, IDENT)
    assert "error" in out  # ValueError caught → error dict


# ---- handle_outcome ----


def test_outcome_writes_log_after_read(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path)
    store.write_note({"type": "note", "title": "N", "summary": "s",
                      "kind": "fact", "verification": "verified",
                      "confidence": "draft", "refs": 0, "last_ref": "", "retired": False})
    read_out = handle_read("memory://notes/N", "", store, IDENT)
    out = handle_outcome(read_out["read_id"], "helpful", "good", tmp_path, IDENT)
    assert out["ok"] is True
    logs = read_outcome_log(tmp_path)
    assert len(logs) == 1
    assert logs[0].outcome == "helpful"
    assert logs[0].memory_id == "N"


def test_outcome_rejects_unknown_read_id(tmp_path: Path) -> None:
    out = handle_outcome("fake-read-id", "helpful", "x", tmp_path, IDENT)
    assert out["error"] == "unknown_read_id"


def test_outcome_rejects_invalid_outcome(tmp_path: Path) -> None:
    """HIGH-4: outcome must be one of the four valid values (schema enum)."""
    out = handle_outcome("any", "badvalue", "x", tmp_path, IDENT)
    assert "invalid outcome" in out["error"]
