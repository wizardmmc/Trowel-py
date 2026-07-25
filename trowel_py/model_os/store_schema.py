"""Model OS Store 的 SQLite schema 与前向迁移步骤。"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from typing import Any

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS events (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id TEXT NOT NULL UNIQUE,
    kind TEXT NOT NULL,
    occurred_at TEXT NOT NULL,
    source TEXT NOT NULL,
    provenance TEXT NOT NULL,
    policy_version TEXT NOT NULL,
    work_item_id TEXT,
    task_id TEXT,
    episode_id TEXT,
    native_session_id TEXT,
    cause_id TEXT,
    correlation_id TEXT,
    outcome TEXT,
    payload TEXT NOT NULL,
    payload_hash TEXT,
    -- fenced 事件持久化授权 lease，用于授权审计和完整幂等重试指纹；
    -- 非 fenced 事件保持 NULL。
    lease_id TEXT,
    owner TEXT,
    fencing_token INTEGER
);

CREATE TABLE IF NOT EXISTS decisions (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    decision_id TEXT NOT NULL UNIQUE,
    kind TEXT NOT NULL,
    disposition TEXT NOT NULL,
    decided_at TEXT NOT NULL,
    work_item_id TEXT,
    task_id TEXT,
    episode_id TEXT,
    cause_id TEXT,
    correlation_id TEXT,
    policy_version TEXT NOT NULL,
    signals TEXT NOT NULL,
    candidates TEXT NOT NULL,
    choice TEXT NOT NULL,
    reason TEXT NOT NULL,
    budget_before TEXT,
    budget_after TEXT,
    identity_hash TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_events_journal_page
    ON events(occurred_at, seq);

CREATE INDEX IF NOT EXISTS idx_decisions_journal_page
    ON decisions(decided_at, seq);

CREATE TABLE IF NOT EXISTS projection_checkpoints (
    projection_name TEXT NOT NULL,
    projection_version INTEGER NOT NULL,
    event_seq INTEGER NOT NULL,
    decision_seq INTEGER NOT NULL,
    state_json TEXT NOT NULL,
    state_hash TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (projection_name, projection_version, event_seq, decision_seq)
);

CREATE TABLE IF NOT EXISTS leases (
    lease_id TEXT PRIMARY KEY,
    resource_type TEXT NOT NULL,
    resource_id TEXT NOT NULL,
    owner TEXT NOT NULL,
    acquired_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    idempotency_key TEXT,
    released_at TEXT,
    -- token 按资源严格递增；零值兼容不需要 fencing 的 lease。
    fencing_token INTEGER NOT NULL DEFAULT 0
);

-- 同一资源最多一个未释放 lease。
CREATE UNIQUE INDEX IF NOT EXISTS idx_leases_active
    ON leases(resource_type, resource_id) WHERE released_at IS NULL;

-- 幂等键按资源隔离，已释放 lease 不阻塞后续授权。
CREATE UNIQUE INDEX IF NOT EXISTS idx_leases_idem
    ON leases(resource_type, resource_id, idempotency_key)
    WHERE idempotency_key IS NOT NULL AND released_at IS NULL;

-- 独立计数器防止清理 lease 历史后 token 回退。
CREATE TABLE IF NOT EXISTS lease_fence_counters (
    resource_type TEXT NOT NULL,
    resource_id TEXT NOT NULL,
    last_token INTEGER NOT NULL,
    PRIMARY KEY (resource_type, resource_id)
);

-- 快照按版本追加；checkpoint_key 保证崩溃重试幂等。
-- reducer 只保存 SnapshotRef；journal_through_seq 防止恢复时重复归约。
CREATE TABLE IF NOT EXISTS episode_snapshots (
    episode_id TEXT NOT NULL,
    version INTEGER NOT NULL,
    checkpoint_key TEXT NOT NULL UNIQUE,
    source TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    payload_hash TEXT NOT NULL,
    -- base SnapshotRef 展平为 episode/version 两列。
    base_episode_id TEXT,
    base_version INTEGER,
    journal_through_seq INTEGER NOT NULL,
    committed_event_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (episode_id, version)
);

-- 前台归属是持久事实而非带期限的 lease。
CREATE TABLE IF NOT EXISTS foreground_claim (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    task_id TEXT
);

-- Task 创建重试必须返回原 Task，空白幂等键在存储层拒绝。
-- SQLite 的非 INTEGER 主键仍可能接受 NULL，因此显式 NOT NULL 不可省略。
CREATE TABLE IF NOT EXISTS task_create_keys (
    idempotency_key TEXT PRIMARY KEY NOT NULL
        CHECK (length(trim(idempotency_key)) > 0),
    task_id TEXT NOT NULL,
    created_at TEXT NOT NULL
);

-- Episode 创建使用相同的幂等约束。
CREATE TABLE IF NOT EXISTS episode_create_keys (
    idempotency_key TEXT PRIMARY KEY NOT NULL
        CHECK (length(trim(idempotency_key)) > 0),
    episode_id TEXT NOT NULL,
    created_at TEXT NOT NULL
);
"""


def migrate_v4_to_v5(
    conn: sqlite3.Connection,
    *,
    decode_decision: Callable[[sqlite3.Row], Any],
    fingerprint: Callable[[Any], str],
) -> None:
    columns = {
        row["name"] for row in conn.execute("PRAGMA table_info(decisions)").fetchall()
    }
    if "disposition" not in columns:
        conn.execute(
            "ALTER TABLE decisions ADD COLUMN disposition TEXT NOT NULL "
            "DEFAULT 'legacy_unknown'"
        )
    if "identity_hash" not in columns:
        conn.execute("ALTER TABLE decisions ADD COLUMN identity_hash TEXT")
    rows = conn.execute("SELECT * FROM decisions ORDER BY seq").fetchall()
    for row in rows:
        conn.execute(
            "UPDATE decisions SET disposition='legacy_unknown', identity_hash=? "
            "WHERE seq=?",
            (fingerprint(decode_decision(row)), int(row["seq"])),
        )
    conn.execute(
        "UPDATE meta SET value=? WHERE key='schema_version'",
        ("5",),
    )


def migrate_v5_to_v6(conn: sqlite3.Connection) -> None:
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_events_journal_page "
        "ON events(occurred_at, seq)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_decisions_journal_page "
        "ON decisions(decided_at, seq)"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS projection_checkpoints ("
        "projection_name TEXT NOT NULL, "
        "projection_version INTEGER NOT NULL, "
        "event_seq INTEGER NOT NULL, "
        "decision_seq INTEGER NOT NULL, "
        "state_json TEXT NOT NULL, "
        "state_hash TEXT NOT NULL, "
        "created_at TEXT NOT NULL, "
        "PRIMARY KEY (projection_name, projection_version, event_seq, decision_seq))"
    )
    conn.execute(
        "UPDATE meta SET value=? WHERE key='schema_version'",
        ("6",),
    )
