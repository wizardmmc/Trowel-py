"""Model OS Event/Decision 账本的共享 SQLite schema。"""

JOURNAL_SCHEMA_SQL = """
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

CREATE INDEX IF NOT EXISTS idx_events_cause_kind_seq
    ON events(cause_id, kind, seq);

CREATE INDEX IF NOT EXISTS idx_events_correlation_kind_seq
    ON events(correlation_id, kind, seq);

CREATE INDEX IF NOT EXISTS idx_events_kind_time_seq
    ON events(kind, occurred_at, seq);

CREATE INDEX IF NOT EXISTS idx_events_task_seq
    ON events(task_id, seq);

CREATE INDEX IF NOT EXISTS idx_events_episode_seq
    ON events(episode_id, seq);

CREATE INDEX IF NOT EXISTS idx_events_work_item_seq
    ON events(work_item_id, seq);

CREATE INDEX IF NOT EXISTS idx_decisions_journal_page
    ON decisions(decided_at, seq);

CREATE INDEX IF NOT EXISTS idx_decisions_kind_time_seq
    ON decisions(kind, decided_at, seq);

CREATE INDEX IF NOT EXISTS idx_decisions_cause_kind_seq
    ON decisions(cause_id, kind, seq);

CREATE INDEX IF NOT EXISTS idx_decisions_task_kind_seq
    ON decisions(task_id, kind, seq);

CREATE INDEX IF NOT EXISTS idx_decisions_episode_kind_seq
    ON decisions(episode_id, kind, seq);

"""
