"""隔离 Agent Host 测试路径，并监测真实会话索引与 memory 数据库。"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

_REAL_DB = Path.home() / ".trowel" / "memory" / "meta" / "sessions.db"
_REAL_BINDINGS = Path.home() / ".trowel" / "agent_sessions.json"
_REAL_DELEGATES = Path.home() / ".trowel" / "agent_sessions.delegates.json"


def _snapshot_file(path: Path) -> int | None:
    if not path.exists():
        return None
    return path.stat().st_mtime_ns


def _snapshot_real_db() -> tuple[int, int | None] | None:
    if not _REAL_DB.exists():
        return None
    mtime = _REAL_DB.stat().st_mtime_ns
    try:
        conn = sqlite3.connect(f"file:{_REAL_DB}?mode=ro", uri=True)
        try:
            count = conn.execute("SELECT count(*) FROM sessions").fetchone()[0]
        finally:
            conn.close()
    except sqlite3.Error:
        count = None
    return (mtime, count)


@pytest.fixture(autouse=True)
def _isolate_agent_home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> object:
    monkeypatch.setenv(
        "TROWEL_AGENT_SESSIONS_PATH", str(tmp_path / "agent_sessions.json")
    )
    monkeypatch.setenv("TROWEL_MCP_CONFIG", str(tmp_path / "memory-mcp-config.json"))
    monkeypatch.setattr(
        "trowel_py.memory.paths.resolve_memory_root",
        lambda config_path=None: tmp_path / "memory",
    )
    before_bindings = _snapshot_file(_REAL_BINDINGS)
    before_delegates = _snapshot_file(_REAL_DELEGATES)
    before_db = _snapshot_real_db()
    yield
    after_bindings = _snapshot_file(_REAL_BINDINGS)
    after_delegates = _snapshot_file(_REAL_DELEGATES)
    after_db = _snapshot_real_db()
    assert before_bindings == after_bindings, (
        f"agent_host test mutated the real agent_sessions.json: "
        f"{before_bindings} -> {after_bindings}"
    )
    assert before_delegates == after_delegates, (
        f"agent_host test mutated the real delegate identity index: "
        f"{before_delegates} -> {after_delegates}"
    )
    assert before_db == after_db, (
        f"agent_host test mutated the real sessions.db: {before_db} -> {after_db}"
    )
