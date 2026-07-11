"""slice-040-b test isolation for the cc_host suite.

Two autouse concerns, both function-scoped:

1. **Redirect** ``resolve_memory_root`` to a per-test tmp dir. The ~35 CCHost
   builds in ``test_service.py`` use the default ``session_registrar=None``
   (they pre-date the injection), so their init handler walks the real-db path
   in ``_register_session_blocking``. Pointing ``resolve_memory_root`` at tmp
   keeps those writes off ``~/.trowel/memory/meta/sessions.db`` without touching
   the 35 call sites (C-2: isolation by dependency injection / redirect, NOT by
   workdir path blacklisting — C-5).

2. **Guard** the real sessions.db (mtime_ns + row count) is unchanged across
   every test — defense-in-depth against a future regression that bypasses the
   redirect.

Shared fakes (FakeWriter/FakeProc/FakeSpawner) are still duplicated in
test_service.py / test_session_registration.py; extracting them is a future
cleanup — the two copies have already diverged (test_service's records
``spawned`` args, the registration copy does not).
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

_REAL_DB = Path.home() / ".trowel" / "memory" / "meta" / "sessions.db"


def _snapshot_real_sessions_db() -> tuple[int, int | None] | None:
    """Read-only snapshot of the real sessions.db: ``(mtime_ns, row_count)``.

    Returns None when the db does not exist (fresh CI). Opens in ``mode=ro`` so
    a snapshot can never itself mutate the file.
    """
    if not _REAL_DB.exists():
        return None
    mtime = _REAL_DB.stat().st_mtime_ns
    count: int | None
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
def _isolate_and_guard_memory_db(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> object:
    """Redirect the memory root to tmp + assert the real sessions.db is untouched."""
    mem = tmp_path / "memory"
    monkeypatch.setattr(
        "trowel_py.memory.paths.resolve_memory_root",
        lambda config_path=None: mem,
    )
    before = _snapshot_real_sessions_db()
    yield
    after = _snapshot_real_sessions_db()
    assert before == after, (
        f"cc_host test mutated the real sessions.db: {before} -> {after}"
    )
