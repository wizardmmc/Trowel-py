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
