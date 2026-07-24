"""memory MCP 读取与反馈处理器。"""

from __future__ import annotations

from pathlib import Path

from tests.memory.mcp.support import IDENTITY
from trowel_py.memory.access_log import read_access_log, read_outcome_log
from trowel_py.memory.mcp_server import handle_outcome, handle_read
from trowel_py.memory.store import MemoryStore


def test_read_records_ref_and_log(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path)
    store.write_note(
        {
            "type": "note",
            "title": "Read Me",
            "summary": "s",
            "kind": "fact",
            "verification": "verified",
            "confidence": "draft",
            "refs": 0,
            "last_ref": "",
            "retired": False,
            "__body": "BODY_MARKER",
        }
    )

    result = handle_read("memory://notes/Read-Me", "search-1", store, IDENTITY)

    assert result["body"] == "BODY_MARKER"
    assert "read_id" in result
    assert store.load_note("Read-Me").refs == 1
    assert any(
        record.action == "read" and record.memory_id == "Read-Me"
        for record in read_access_log(tmp_path)
    )


def test_read_unknown_returns_not_found(tmp_path: Path) -> None:
    result = handle_read(
        "memory://notes/nope",
        "",
        MemoryStore(tmp_path),
        IDENTITY,
    )

    assert result["error"] == "not_found"


def test_read_illegal_uri_raises(tmp_path: Path) -> None:
    result = handle_read(
        "memory://notes/../etc/passwd",
        "",
        MemoryStore(tmp_path),
        IDENTITY,
    )

    assert "error" in result


def test_outcome_writes_log_after_read(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path)
    store.write_note(
        {
            "type": "note",
            "title": "N",
            "summary": "s",
            "kind": "fact",
            "verification": "verified",
            "confidence": "draft",
            "refs": 0,
            "last_ref": "",
            "retired": False,
        }
    )
    read_result = handle_read("memory://notes/N", "", store, IDENTITY)

    result = handle_outcome(
        read_result["read_id"],
        "helpful",
        "good",
        tmp_path,
        IDENTITY,
    )

    assert result["ok"] is True
    records = read_outcome_log(tmp_path)
    assert len(records) == 1
    assert records[0].outcome == "helpful"
    assert records[0].memory_id == "N"


def test_outcome_rejects_unknown_read_id(tmp_path: Path) -> None:
    result = handle_outcome(
        "fake-read-id",
        "helpful",
        "x",
        tmp_path,
        IDENTITY,
    )

    assert result["error"] == "unknown_read_id"


def test_outcome_rejects_invalid_outcome(tmp_path: Path) -> None:
    result = handle_outcome(
        "any",
        "badvalue",
        "x",
        tmp_path,
        IDENTITY,
    )

    assert "invalid outcome" in result["error"]
