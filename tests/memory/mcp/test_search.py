"""memory MCP 搜索处理器。"""

from __future__ import annotations

from pathlib import Path

from tests.memory.mcp.support import IDENTITY, FakeRetriever
from trowel_py.memory.access_log import read_access_log
from trowel_py.memory.mcp_server import handle_search
from trowel_py.memory.store import MemoryStore


def test_search_dictionary_empty_returns_hint(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path)

    result = handle_search(
        "q",
        5,
        False,
        store,
        tmp_path / "dictionary-L0.md",
        IDENTITY,
    )

    assert result["results"] == []
    assert result["error"] == "dictionary_empty"
    assert "dict-rebuild" in result["hint"]
    assert any(
        record.action == "search" and record.query == "q"
        for record in read_access_log(tmp_path)
    )


def test_search_with_dictionary_returns_hits(tmp_path: Path) -> None:
    dictionary_path = tmp_path / "dictionary-L0.md"
    dictionary_path.write_text("L0", encoding="utf-8")
    store = MemoryStore(tmp_path)
    store.write_note(
        {
            "type": "note",
            "title": "Note One",
            "summary": "first",
            "kind": "gotcha",
            "verification": "inferred-untested",
            "confidence": "draft",
            "refs": 0,
            "last_ref": "",
            "retired": False,
        }
    )

    result = handle_search(
        "note",
        5,
        False,
        store,
        dictionary_path,
        IDENTITY,
        retriever=FakeRetriever(["Note-One"]),
    )

    assert len(result["results"]) == 1
    hit = result["results"][0]
    assert hit["memory_id"] == "Note-One"
    assert hit["uri"] == "memory://notes/Note-One"
    assert hit["requires_read"] is True


def test_search_filters_retired(tmp_path: Path) -> None:
    dictionary_path = tmp_path / "dictionary-L0.md"
    dictionary_path.write_text("L0", encoding="utf-8")
    store = MemoryStore(tmp_path)
    store.write_note(
        {
            "type": "note",
            "title": "Retired One",
            "summary": "r",
            "kind": "fact",
            "verification": "verified",
            "confidence": "draft",
            "refs": 0,
            "last_ref": "",
            "retired": True,
        }
    )

    result = handle_search(
        "r",
        5,
        False,
        store,
        dictionary_path,
        IDENTITY,
        retriever=FakeRetriever(["Retired-One"]),
    )

    assert result["results"] == []
