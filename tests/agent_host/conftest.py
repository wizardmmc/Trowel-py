"""slice-072 test isolation for the agent_host suite.

Autouse, function-scoped — three belt-and-braces concerns (spec C-7: tests
never touch the real sessions DB or the user's home):

1. Pin ``TROWEL_AGENT_SESSIONS_PATH`` to tmp so :class:`BindingStore` never
   writes the real ``~/.trowel/agent_sessions.json``.
2. Redirect ``resolve_memory_root`` + ``TROWEL_MCP_CONFIG`` to tmp — the CC
   branch builds ``CCHost`` which otherwise writes
   ``~/.trowel/memory-mcp-config.json`` and walks the real sessions.db
   (identical concerns to ``tests/cc_host/conftest.py``).
3. Snapshot the real ``agent_sessions.json`` + ``sessions.db`` before/after
   every test and assert they are unchanged — defense-in-depth against a
   future regression that bypasses the redirects.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

_REAL_DB = Path.home() / ".trowel" / "memory" / "meta" / "sessions.db"
_REAL_BINDINGS = Path.home() / ".trowel" / "agent_sessions.json"


def _snapshot_file(path: Path) -> int | None:
    """mtime_ns of a file, or None when it does not exist."""

    if not path.exists():
        return None
    return path.stat().st_mtime_ns


def _snapshot_real_db() -> tuple[int, int | None] | None:
    """Read-only ``(mtime_ns, row_count)`` of the real sessions.db."""

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
def _isolate_agent_home(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> object:
    """Redirect every home-writing path to tmp + assert real files untouched."""

    monkeypatch.setenv(
        "TROWEL_AGENT_SESSIONS_PATH", str(tmp_path / "agent_sessions.json")
    )
    monkeypatch.setenv(
        "TROWEL_MCP_CONFIG", str(tmp_path / "memory-mcp-config.json")
    )
    monkeypatch.setattr(
        "trowel_py.memory.paths.resolve_memory_root",
        lambda config_path=None: tmp_path / "memory",
    )
    before_bindings = _snapshot_file(_REAL_BINDINGS)
    before_db = _snapshot_real_db()
    yield
    after_bindings = _snapshot_file(_REAL_BINDINGS)
    after_db = _snapshot_real_db()
    assert before_bindings == after_bindings, (
        f"agent_host test mutated the real agent_sessions.json: "
        f"{before_bindings} -> {after_bindings}"
    )
    assert before_db == after_db, (
        f"agent_host test mutated the real sessions.db: {before_db} -> {after_db}"
    )
