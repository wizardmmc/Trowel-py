"""Schema 迁移与 registrar 协议。"""

from __future__ import annotations

import sqlite3

from trowel_py.memory.sessions_repo import (
    SessionRecord,
    SessionRegistrar,
    create_sessions_repository,
)


def test_session_registrar_protocol_accepts_fake() -> None:
    class FakeRegistrar:
        def register(self, rec: SessionRecord) -> None:
            self.recorded = rec

        def update_completed(
            self,
            cc_session_id: str,
            completed_bytes: int,
            when: str | None = None,
        ) -> None:
            self.completed = (cc_session_id, completed_bytes)

    assert isinstance(FakeRegistrar(), SessionRegistrar)


def test_old_schema_migrates_offset_columns(tmp_path) -> None:
    database = tmp_path / "sessions.db"
    conn = sqlite3.connect(str(database))
    conn.executescript(
        "CREATE TABLE sessions ("
        "cc_session_id TEXT PRIMARY KEY, workdir TEXT NOT NULL,"
        " date TEXT NOT NULL, jsonl_path TEXT,"
        " registered_at TEXT NOT NULL, extracted_at TEXT);"
    )
    conn.execute(
        "INSERT INTO sessions(cc_session_id, workdir, date, registered_at)"
        " VALUES ('legacy', '/workspace', '2026-07-09', 't')"
    )
    conn.commit()
    conn.close()

    conn = sqlite3.connect(str(database))
    repo = create_sessions_repository(conn)
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(sessions)")}
    assert {
        "session_kind",
        "last_completed_offset",
        "last_completed_at",
        "last_extracted_offset",
        "last_extracted_at",
    } <= columns
    assert len(repo.find_pending("2026-07-09")) == 1
    conn.close()


def test_ensure_columns_idempotent(tmp_path) -> None:
    database = tmp_path / "sessions.db"
    first = sqlite3.connect(str(database))
    create_sessions_repository(first)
    first.close()
    second = sqlite3.connect(str(database))
    create_sessions_repository(second)
    second.close()


def test_old_codex_schema_migrates_review_fragment_column(tmp_path) -> None:
    database = tmp_path / "sessions.db"
    conn = sqlite3.connect(str(database))
    conn.executescript(
        "CREATE TABLE sessions ("
        "cc_session_id TEXT PRIMARY KEY, workdir TEXT NOT NULL,"
        " date TEXT NOT NULL, jsonl_path TEXT,"
        " registered_at TEXT NOT NULL, extracted_at TEXT);"
        "CREATE TABLE codex_turns ("
        "thread_id TEXT NOT NULL, turn_id TEXT NOT NULL,"
        "trowel_session_id TEXT NOT NULL, workdir TEXT NOT NULL,"
        "journal_path TEXT NOT NULL, registered_at TEXT NOT NULL,"
        "status TEXT NOT NULL DEFAULT 'running', completed_at TEXT,"
        "extracted_at TEXT, model TEXT NOT NULL DEFAULT '',"
        "effort TEXT NOT NULL DEFAULT '', provider TEXT NOT NULL DEFAULT '',"
        "memory_enabled INTEGER NOT NULL DEFAULT 1,"
        "profile_enabled INTEGER NOT NULL DEFAULT 1,"
        "session_kind TEXT NOT NULL DEFAULT 'user',"
        "PRIMARY KEY (thread_id, turn_id));"
    )
    conn.execute(
        "INSERT INTO codex_turns("
        "thread_id, turn_id, trowel_session_id, workdir, journal_path,"
        "registered_at, completed_at"
        ") VALUES ('thread-1', 'turn-1', 'trowel-1', '/workspace',"
        "'/journal/turn-1.jsonl', '2026-07-09T10:00:00',"
        "'2026-07-09T10:05:00')"
    )
    conn.commit()
    conn.close()

    migrated = sqlite3.connect(str(database))
    repo = create_sessions_repository(migrated)
    columns = {
        row["name"] for row in migrated.execute("PRAGMA table_info(codex_turns)")
    }
    [fragment] = repo.find_incremental_codex()

    assert "review_fragment_id" in columns
    assert fragment.turn_ids == ("turn-1",)
    migrated.close()
