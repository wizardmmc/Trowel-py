import sqlite3
from pathlib import Path

import pytest

from tests.cc_host.session_registration.support import (
    FakeProc,
    FakeSpawner,
    init_event,
    line,
    patch_memory_root,
    result_event,
)
from trowel_py.cc_host.service import CCHost
from trowel_py.schemas.cc_host import FinishedEvent


async def test_register_session_writes_db(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    root = patch_memory_root(monkeypatch, tmp_path)
    jsonl = tmp_path / "session.jsonl"
    jsonl.write_bytes(b"x" * 37)
    host = CCHost("trowel-session", tmp_path, spawner=FakeSpawner([]))
    host._cc_session_id = "cc-session-1"
    host._jsonl_path = lambda session_id: jsonl  # type: ignore[assignment]

    await host._maybe_register_session("cc-session-1")
    await host._maybe_update_completed()

    connection = sqlite3.connect(str(root / "meta" / "sessions.db"))
    connection.row_factory = sqlite3.Row
    try:
        rows = connection.execute("SELECT * FROM sessions").fetchall()
        bindings = connection.execute("SELECT * FROM session_bindings").fetchall()
    finally:
        connection.close()

    assert len(rows) == 1
    assert rows[0]["cc_session_id"] == "cc-session-1"
    assert rows[0]["workdir"] == str(tmp_path)
    assert rows[0]["jsonl_path"] == str(jsonl)
    assert rows[0]["date"] == rows[0]["registered_at"][:10]
    assert rows[0]["session_kind"] == "user"
    assert rows[0]["last_completed_offset"] == 37
    assert len(bindings) == 1
    assert bindings[0]["trowel_session_id"] == "trowel-session"


async def test_init_handler_registers_session(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    root = patch_memory_root(monkeypatch, tmp_path)
    process = FakeProc([line(init_event("cc-from-init")), line(result_event())])
    host = CCHost(
        "trowel-session",
        tmp_path,
        spawner=FakeSpawner([process]),
    )
    host._jsonl_path = lambda session_id: tmp_path / "missing.jsonl"  # type: ignore[assignment]

    async for _ in host.send("hi"):
        pass

    connection = sqlite3.connect(str(root / "meta" / "sessions.db"))
    connection.row_factory = sqlite3.Row
    try:
        rows = connection.execute("SELECT * FROM sessions").fetchall()
    finally:
        connection.close()
    assert len(rows) == 1
    assert rows[0]["cc_session_id"] == "cc-from-init"


async def test_register_failure_does_not_break_session(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    def fail_memory_root(config_path=None):  # type: ignore[no-untyped-def]
        raise OSError("disk full")

    monkeypatch.setattr(
        "trowel_py.memory.paths.resolve_memory_root",
        fail_memory_root,
    )
    process = FakeProc([line(init_event("cc-1")), line(result_event())])
    host = CCHost(
        "trowel-session",
        tmp_path,
        spawner=FakeSpawner([process]),
    )
    host._jsonl_path = lambda session_id: tmp_path / "missing.jsonl"  # type: ignore[assignment]

    events = [event async for event in host.send("hi")]

    assert any(isinstance(event, FinishedEvent) for event in events)
