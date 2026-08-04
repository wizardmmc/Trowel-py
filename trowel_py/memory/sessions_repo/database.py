"""Sessions registry 的 SQLite schema、迁移与连接。"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from trowel_py.telemetry.sqlite import open_observed_sqlite

from .models import (
    CodexTurnRecord,
    ReviewRequest,
    SessionBinding,
    SessionProblemRecord,
    SessionRecord,
)

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
    bound_at          TEXT NOT NULL,
    start_offset      INTEGER,
    status            TEXT NOT NULL DEFAULT 'running',
    completed_at      TEXT
);
CREATE INDEX IF NOT EXISTS idx_bindings_cc ON session_bindings(cc_session_id);
CREATE TABLE IF NOT EXISTS codex_turns (
    thread_id           TEXT NOT NULL,
    turn_id             TEXT NOT NULL,
    trowel_session_id   TEXT NOT NULL,
    workdir             TEXT NOT NULL,
    journal_path        TEXT NOT NULL,
    registered_at       TEXT NOT NULL,
    status              TEXT NOT NULL DEFAULT 'running',
    completed_at        TEXT,
    extracted_at        TEXT,
    model               TEXT NOT NULL DEFAULT '',
    effort              TEXT NOT NULL DEFAULT '',
    provider            TEXT NOT NULL DEFAULT '',
    memory_enabled      INTEGER NOT NULL DEFAULT 1,
    profile_enabled     INTEGER NOT NULL DEFAULT 1,
    session_kind        TEXT NOT NULL DEFAULT 'user',
    review_fragment_id  TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (thread_id, turn_id)
);
CREATE INDEX IF NOT EXISTS idx_codex_turns_incremental
    ON codex_turns(completed_at, extracted_at);
CREATE TABLE IF NOT EXISTS session_review_requests (
    trowel_session_id  TEXT PRIMARY KEY,
    runtime            TEXT NOT NULL,
    requested_at       TEXT NOT NULL,
    not_before         TEXT NOT NULL,
    closed_at          TEXT NOT NULL DEFAULT '',
    native_session_id  TEXT NOT NULL DEFAULT '',
    source_start_offset INTEGER,
    source_end_offset   INTEGER,
    problem_recorded_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_session_review_requests_order
    ON session_review_requests(requested_at, trowel_session_id);
CREATE TABLE IF NOT EXISTS session_review_problems (
    trowel_session_id   TEXT PRIMARY KEY,
    runtime             TEXT NOT NULL,
    closed_at           TEXT NOT NULL,
    closed_at_epoch_us  INTEGER NOT NULL,
    problem_text        TEXT,
    reviewed_at         TEXT NOT NULL,
    pipeline_version    INTEGER NOT NULL,
    run_id              TEXT NOT NULL,
    generator_runtime   TEXT NOT NULL,
    generator_model     TEXT NOT NULL,
    generator_effort    TEXT NOT NULL,
    source_quality      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_session_review_problems_closed
    ON session_review_problems(closed_at_epoch_us DESC, trowel_session_id DESC);
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

_CODEX_ADD_COLUMN_SQL = {
    "session_kind": (
        "ALTER TABLE codex_turns ADD COLUMN session_kind TEXT NOT NULL DEFAULT 'user'"
    ),
    "review_fragment_id": (
        "ALTER TABLE codex_turns ADD COLUMN review_fragment_id TEXT NOT NULL DEFAULT ''"
    ),
}

_BINDING_ADD_COLUMN_SQL = {
    "start_offset": "ALTER TABLE session_bindings ADD COLUMN start_offset INTEGER",
    "status": (
        "ALTER TABLE session_bindings ADD COLUMN status TEXT NOT NULL DEFAULT 'unknown'"
    ),
    "completed_at": "ALTER TABLE session_bindings ADD COLUMN completed_at TEXT",
}

_REVIEW_REQUEST_ADD_COLUMN_SQL = {
    "not_before": (
        "ALTER TABLE session_review_requests"
        " ADD COLUMN not_before TEXT NOT NULL DEFAULT ''"
    ),
    "native_session_id": (
        "ALTER TABLE session_review_requests"
        " ADD COLUMN native_session_id TEXT NOT NULL DEFAULT ''"
    ),
    "closed_at": (
        "ALTER TABLE session_review_requests"
        " ADD COLUMN closed_at TEXT NOT NULL DEFAULT ''"
    ),
    "source_start_offset": (
        "ALTER TABLE session_review_requests ADD COLUMN source_start_offset INTEGER"
    ),
    "source_end_offset": (
        "ALTER TABLE session_review_requests ADD COLUMN source_end_offset INTEGER"
    ),
    "problem_recorded_at": (
        "ALTER TABLE session_review_requests ADD COLUMN problem_recorded_at TEXT"
    ),
}


def initialize_schema(conn: sqlite3.Connection) -> None:
    """创建基础表与索引，再补齐旧库列和增量索引。

    本函数不显式调用 ``commit()``。默认 legacy transaction control 下，
    ``executescript()`` 会先提交已有事务，脚本及随后未包在显式事务中的 DDL
    会自动提交；其他 transaction/autocommit 配置下由连接模式决定。
    """
    conn.executescript(_CREATE_SQL)
    ensure_columns(conn)


def ensure_columns(conn: sqlite3.Connection) -> None:
    """补齐旧 sessions、binding、Codex turn 和 review queue 的兼容列。"""
    existing = {row["name"] for row in conn.execute("PRAGMA table_info(sessions)")}
    for column, sql in _ADD_COLUMN_SQL.items():
        if column not in existing:
            conn.execute(sql)
    codex_existing = {
        row["name"] for row in conn.execute("PRAGMA table_info(codex_turns)")
    }
    for column, sql in _CODEX_ADD_COLUMN_SQL.items():
        if column not in codex_existing:
            conn.execute(sql)
    binding_existing = {
        row["name"] for row in conn.execute("PRAGMA table_info(session_bindings)")
    }
    for column, sql in _BINDING_ADD_COLUMN_SQL.items():
        if column not in binding_existing:
            conn.execute(sql)
    review_request_existing = {
        row["name"]
        for row in conn.execute("PRAGMA table_info(session_review_requests)")
    }
    for column, sql in _REVIEW_REQUEST_ADD_COLUMN_SQL.items():
        if column not in review_request_existing:
            conn.execute(sql)
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_sessions_incremental"
        " ON sessions(last_completed_offset, last_extracted_offset)"
    )


def open_sessions_db(memory_root: Path) -> sqlite3.Connection:
    """打开可写数据库，并让查询结果支持按列名访问。

    会创建 ``meta`` 目录，SQLite 也可能创建 ``sessions.db``；本函数不初始化
    schema，调用方负责关闭连接。

    Raises:
        OSError: ``meta`` 目录无法创建。
        sqlite3.Error: 数据库连接失败。
    """
    meta = memory_root / _META_DIR
    meta.mkdir(parents=True, exist_ok=True)
    conn = open_observed_sqlite(meta / _SESSIONS_DB, domain="sessions")
    conn.row_factory = sqlite3.Row
    return conn


def open_sessions_db_readonly(
    memory_root: Path,
) -> sqlite3.Connection | None:
    """以 SQLite URI 只读模式打开现有数据库。

    路径不存在时返回 None，不创建目录、文件或 schema。URI 直接拼接本地路径，
    不对 ``?``、``#`` 等字符编码；调用方负责提供可信路径并关闭返回的连接。

    Returns:
        支持按列名访问的只读连接，或在路径不存在时返回 None。

    Raises:
        OSError: 数据库路径检查失败。
        sqlite3.Error: 路径存在但无法按只读数据库打开。
    """
    database = memory_root / _META_DIR / _SESSIONS_DB
    if not database.exists():
        return None
    conn = open_observed_sqlite(
        f"file:{database}?mode=ro",
        domain="sessions",
        uri=True,
    )
    conn.row_factory = sqlite3.Row
    return conn


def row_to_record(row: sqlite3.Row) -> SessionRecord:
    """把完整 sessions 行转换为 CC 会话记录。

    ``jsonl_path`` 的假值变为空字符串，``session_kind`` 的假值回退为 ``user``。
    sessions 表没有 trowel id，因此模型保留该字段的默认空值。缺列异常传播，列值
    类型不在此处校验。
    """
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
    """把完整 session_bindings 行转换为会话绑定。

    所有列原样传入；缺列异常传播，列值类型不在此处校验。
    """
    return SessionBinding(
        trowel_session_id=row["trowel_session_id"],
        cc_session_id=row["cc_session_id"],
        session_kind=row["session_kind"],
        workdir=row["workdir"],
        bound_at=row["bound_at"],
        start_offset=row["start_offset"],
        status=row["status"] or "unknown",
        completed_at=row["completed_at"],
    )


def row_to_codex_turn(row: sqlite3.Row) -> CodexTurnRecord:
    """把完整 codex_turns 行转换为 Codex 轮次记录。

    model、effort 和 provider 的假值变为空字符串；两个开关通过 ``bool()``
    转换；session kind 的假值回退为 ``user``。其他列原样传入；缺列异常传播，
    列值类型不在此处校验。
    """
    return CodexTurnRecord(
        thread_id=row["thread_id"],
        turn_id=row["turn_id"],
        trowel_session_id=row["trowel_session_id"],
        workdir=row["workdir"],
        journal_path=row["journal_path"],
        registered_at=row["registered_at"],
        status=row["status"],
        completed_at=row["completed_at"],
        extracted_at=row["extracted_at"],
        model=row["model"] or "",
        effort=row["effort"] or "",
        provider=row["provider"] or "",
        memory_enabled=bool(row["memory_enabled"]),
        profile_enabled=bool(row["profile_enabled"]),
        session_kind=row["session_kind"] or "user",
        review_fragment_id=row["review_fragment_id"] or "",
    )


def row_to_review_request(row: sqlite3.Row) -> ReviewRequest:
    """把即时 review 请求行转换为持久队列记录。"""

    return ReviewRequest(
        trowel_session_id=row["trowel_session_id"],
        runtime=row["runtime"],
        requested_at=row["requested_at"],
        not_before=row["not_before"] or row["requested_at"],
        closed_at=row["closed_at"] or row["requested_at"],
        native_session_id=row["native_session_id"] or "",
        source_start_offset=row["source_start_offset"],
        source_end_offset=row["source_end_offset"],
        problem_recorded_at=row["problem_recorded_at"],
    )


def row_to_session_problem(row: sqlite3.Row) -> SessionProblemRecord:
    """把会话问题表的一行转换为冻结记录。"""

    return SessionProblemRecord(
        trowel_session_id=row["trowel_session_id"],
        runtime=row["runtime"],
        closed_at=row["closed_at"],
        problem_text=row["problem_text"],
        reviewed_at=row["reviewed_at"],
        pipeline_version=int(row["pipeline_version"]),
        run_id=row["run_id"],
        generator_runtime=row["generator_runtime"],
        generator_model=row["generator_model"],
        generator_effort=row["generator_effort"],
        source_quality=row["source_quality"],
    )
