"""Sessions registry 的 SQLite schema、迁移与连接。"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from .models import SessionBinding, SessionRecord

_META_DIR = "meta"
_SESSIONS_DB = "sessions.db"

_CREATE_SQL = """
CREATE TABLE IF NOT EXISTS sessions (
    cc_session_id         TEXT PRIMARY KEY,
    workdir               TEXT NOT NULL,
    date                  TEXT NOT NULL,
    jsonl_path            TEXT,
    registered_at         TEXT NOT NULL,
    extracted_at          TEXT,
    session_kind          TEXT DEFAULT 'user',
    last_completed_offset INTEGER,
    last_completed_at     TEXT,
    last_extracted_offset INTEGER,
    last_extracted_at     TEXT
);
CREATE INDEX IF NOT EXISTS idx_sessions_date ON sessions(date);
CREATE TABLE IF NOT EXISTS session_bindings (
    trowel_session_id TEXT PRIMARY KEY,
    cc_session_id     TEXT NOT NULL,
    session_kind      TEXT NOT NULL,
    workdir           TEXT NOT NULL,
    bound_at          TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_bindings_cc ON session_bindings(cc_session_id);
"""

_ADD_COLUMN_SQL = {
    "session_kind": (
        "ALTER TABLE sessions ADD COLUMN session_kind TEXT DEFAULT 'user'"
    ),
    "last_completed_offset": (
        "ALTER TABLE sessions ADD COLUMN last_completed_offset INTEGER"
    ),
    "last_completed_at": ("ALTER TABLE sessions ADD COLUMN last_completed_at TEXT"),
    "last_extracted_offset": (
        "ALTER TABLE sessions ADD COLUMN last_extracted_offset INTEGER"
    ),
    "last_extracted_at": ("ALTER TABLE sessions ADD COLUMN last_extracted_at TEXT"),
}


def initialize_schema(conn: sqlite3.Connection) -> None:
    """建表后补齐旧库列，最后创建依赖新增列的索引。"""
    conn.executescript(_CREATE_SQL)
    ensure_columns(conn)


def ensure_columns(conn: sqlite3.Connection) -> None:
    """只补齐旧 sessions 表缺失的列和增量索引。"""
    existing = {row["name"] for row in conn.execute("PRAGMA table_info(sessions)")}
    for column, sql in _ADD_COLUMN_SQL.items():
        if column not in existing:
            conn.execute(sql)
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_sessions_incremental"
        " ON sessions(last_completed_offset, last_extracted_offset)"
    )


def open_sessions_db(memory_root: Path) -> sqlite3.Connection:
    meta = memory_root / _META_DIR
    meta.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(meta / _SESSIONS_DB))
    conn.row_factory = sqlite3.Row
    return conn


def open_sessions_db_readonly(
    memory_root: Path,
) -> sqlite3.Connection | None:
    """只读打开现有数据库；缺失时不创建目录或文件。"""
    database = memory_root / _META_DIR / _SESSIONS_DB
    if not database.exists():
        return None
    conn = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def row_to_record(row: sqlite3.Row) -> SessionRecord:
    return SessionRecord(
        cc_session_id=row["cc_session_id"],
        workdir=row["workdir"],
        date=row["date"],
        jsonl_path=row["jsonl_path"] or "",
        registered_at=row["registered_at"],
        extracted_at=row["extracted_at"],
        session_kind=row["session_kind"] or "user",
        last_completed_offset=row["last_completed_offset"],
        last_completed_at=row["last_completed_at"],
        last_extracted_offset=row["last_extracted_offset"],
        last_extracted_at=row["last_extracted_at"],
    )


def row_to_binding(row: sqlite3.Row) -> SessionBinding:
    return SessionBinding(
        trowel_session_id=row["trowel_session_id"],
        cc_session_id=row["cc_session_id"],
        session_kind=row["session_kind"],
        workdir=row["workdir"],
        bound_at=row["bound_at"],
    )
