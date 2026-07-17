"""slice-040 T4: cc session-start registers into the memory sessions db.

The init handler fires ``_maybe_register_session`` once cc_session_id is
learned. Registration is fire-and-forget: a memory subsystem failure must
never break the cc session. pytest-asyncio auto mode runs the async tests.
"""
from __future__ import annotations

import asyncio
import json
import sqlite3
from pathlib import Path

import pytest

from trowel_py.cc_host.service import CCHost
from trowel_py.memory.sessions_repo import SessionRecord


# --- light fakes (subset of tests/cc_host/test_service.py) ---


class FakeWriter:
    def __init__(self) -> None:
        self.written: list[bytes] = []

    def write(self, data: bytes) -> None:
        self.written.append(data)

    async def drain(self) -> None:
        return None

    def close(self) -> None:
        pass

    def write_eof(self) -> None:
        pass


class FakeProc:
    def __init__(self, lines: list[str], feed_eof: bool = True) -> None:
        self.pid = 1
        self.returncode: int | None = None
        self.stdin = FakeWriter()
        self.stdout = asyncio.StreamReader()
        for ln in lines:
            self.stdout.feed_data(ln.encode() + b"\n")
        if feed_eof:
            self.stdout.feed_eof()
        self.stderr = asyncio.StreamReader()
        self.stderr.feed_eof()

    def terminate(self) -> None:
        self.returncode = 0

    def kill(self) -> None:
        self.returncode = -9

    async def wait(self) -> int | None:
        return self.returncode


class FakeSpawner:
    def __init__(self, procs: list[FakeProc]) -> None:
        self._procs = list(procs)

    async def __call__(self, args: list[str], kwargs: dict) -> FakeProc:
        return self._procs.pop(0)


def _line(d: dict) -> str:
    return json.dumps(d)


def _init(sid: str = "cc-sid-1") -> dict:
    return {
        "type": "system",
        "subtype": "init",
        "model": "glm-5.2",
        "cwd": "/wd",
        "session_id": sid,
        "tools": ["Read"],
    }


def _result() -> dict:
    return {
        "type": "result",
        "subtype": "success",
        "is_error": False,
        "total_cost_usd": 0.03,
        "usage": {"input_tokens": 5},
        "num_turns": 1,
    }


def _patch_memory_root(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Redirect the memory root to tmp_path so tests never touch ~/.trowel."""
    monkeypatch.setattr(
        "trowel_py.memory.paths.resolve_memory_root",
        lambda config_path=None: tmp_path,
    )
    return tmp_path


async def test_register_session_writes_db(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """_maybe_register_session writes one row to sessions.db."""
    root = _patch_memory_root(monkeypatch, tmp_path)
    host = CCHost("trowel-sid", tmp_path, spawner=FakeSpawner([]))
    host._cc_session_id = "cc-uuid-1"
    await host._maybe_register_session("cc-uuid-1")

    conn = sqlite3.connect(str(root / "meta" / "sessions.db"))
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT * FROM sessions").fetchall()
    conn.close()
    assert len(rows) == 1
    assert rows[0]["cc_session_id"] == "cc-uuid-1"
    assert rows[0]["workdir"] == str(tmp_path)


async def test_init_handler_registers_session(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """End-to-end (fake cc): the init event triggers registration."""
    root = _patch_memory_root(monkeypatch, tmp_path)
    proc = FakeProc([_line(_init("cc-from-init")), _line(_result())])
    host = CCHost("trowel-sid", tmp_path, spawner=FakeSpawner([proc]))

    async for _ in host.send("hi"):
        pass

    conn = sqlite3.connect(str(root / "meta" / "sessions.db"))
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT * FROM sessions").fetchall()
    conn.close()
    assert len(rows) == 1
    assert rows[0]["cc_session_id"] == "cc-from-init"


async def test_register_failure_does_not_break_session(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A memory subsystem failure (unwritable root) must not break cc."""

    def boom(config_path=None):  # type: ignore[no-untyped-def]
        raise OSError("disk full")

    monkeypatch.setattr("trowel_py.memory.paths.resolve_memory_root", boom)
    proc = FakeProc([_line(_init("cc-1")), _line(_result())])
    host = CCHost("trowel-sid", tmp_path, spawner=FakeSpawner([proc]))

    events = [e async for e in host.send("hi")]
    assert len(events) > 0  # session completed despite register failure


# --- slice-040-b: injectable session_registrar (C-2 isolation) --------------


class _CapturingRegistrar:
    """Duck-typed SessionRegistrar that records calls (no real db touched)."""

    def __init__(self) -> None:
        self.registered: list[SessionRecord] = []
        self.completed: list[tuple[str, int]] = []

    def register(self, rec: SessionRecord) -> None:
        self.registered.append(rec)

    def update_completed(
        self, cc_session_id: str, completed_bytes: int, when: str | None = None
    ) -> None:
        self.completed.append((cc_session_id, completed_bytes))


async def test_cchost_uses_injected_registrar(tmp_path: Path) -> None:
    """An injected registrar receives the SessionRecord instead of the real db."""
    cap = _CapturingRegistrar()
    proc = FakeProc([_line(_init("cc-inj")), _line(_result())])
    host = CCHost(
        "trowel-sid",
        tmp_path,
        spawner=FakeSpawner([proc]),
        session_registrar=cap,
        session_kind="review",
    )

    async for _ in host.send("hi"):
        pass

    assert len(cap.registered) == 1
    rec = cap.registered[0]
    assert rec.cc_session_id == "cc-inj"
    assert rec.session_kind == "review"  # kind flows through to the registrar
    assert rec.workdir == str(tmp_path)
    # slice-061: trowel session id flows through so register() can bind it.
    assert rec.trowel_session_id == "trowel-sid"


async def test_cchost_default_registrar_is_none(tmp_path: Path) -> None:
    """Default session_registrar is None (falls back to the real-db path)."""
    host = CCHost("trowel-sid", tmp_path, spawner=FakeSpawner([]))
    assert host._session_registrar is None  # noqa: SLF001 — assert the default
    assert host._session_kind == "user"  # noqa: SLF001


async def test_normal_end_updates_completed(tmp_path: Path) -> None:
    """The result turn boundary stamps the completed water mark (slice-040-b C-6).

    Half-turns (no result) never call update_completed, so they stay out of
    find_incremental. The jsonl byte size is read off the path the registrar is
    given; here we stub _jsonl_path to a known-size tmp file.
    """
    cap = _CapturingRegistrar()
    jsonl = tmp_path / "fake.jsonl"
    jsonl.write_text("x" * 100)
    proc = FakeProc([_line(_init("cc-wm")), _line(_result())])
    host = CCHost(
        "trowel-sid",
        tmp_path,
        spawner=FakeSpawner([proc]),
        session_registrar=cap,
    )
    host._jsonl_path = lambda sid: jsonl  # type: ignore[assignment] — stub for the test
    async for _ in host.send("hi"):
        pass

    assert len(cap.completed) == 1
    assert cap.completed[0] == ("cc-wm", 100)


async def test_both_switches_off_still_registers_and_stamps_completed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """slice-060 C-4: closing the READ switches does not close the WRITE loop.

    A memory=off + profile=off experiment session must STILL register into the
    sessions db and stamp its completed offset, so it stays distillable by the
    daily review afterwards. This is a characterization test (the registration
    code never reads the switches) — it pins the invariant against future
    regressions, e.g. someone gating registration on memory_enabled.
    """
    monkeypatch.setattr(
        "trowel_py.memory.injection.resolve_memory_root",
        lambda config_path=None: tmp_path,
    )
    cap = _CapturingRegistrar()
    jsonl = tmp_path / "fake.jsonl"
    jsonl.write_text("x" * 100)
    proc = FakeProc([_line(_init("cc-off")), _line(_result())])
    host = CCHost(
        "trowel-sid",
        tmp_path,
        spawner=FakeSpawner([proc]),
        session_registrar=cap,
        memory_enabled=False,
        profile_enabled=False,
    )
    host._jsonl_path = lambda sid: jsonl  # type: ignore[assignment] — stub for the test

    async for _ in host.send("hi"):
        pass

    # C-4: the write loop is intact — register + completed both fired.
    assert len(cap.registered) == 1
    assert cap.registered[0].cc_session_id == "cc-off"
    assert len(cap.completed) == 1
    assert cap.completed[0] == ("cc-off", 100)
    # sanity: the read switches really did take effect on this host.
    assert host.memory_enabled is False
    assert host.profile_enabled is False
    assert host._mcp_config is None  # noqa: SLF001 — C-3 read-path closed


