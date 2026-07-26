"""Incubation 在 Model OS SQLite 中的隔离表。"""

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS incubation_plans (
    plan_id TEXT PRIMARY KEY,
    command_id TEXT NOT NULL UNIQUE,
    command_fingerprint TEXT NOT NULL,
    task_id TEXT NOT NULL,
    work_item_id TEXT NOT NULL,
    prepared_snapshot_json TEXT NOT NULL,
    unresolved_question TEXT NOT NULL,
    wake_condition_json TEXT NOT NULL,
    deadline TEXT,
    budget_json TEXT NOT NULL,
    runtime TEXT NOT NULL,
    cycle INTEGER NOT NULL DEFAULT 0,
    max_scheduled_cycles INTEGER NOT NULL,
    status TEXT NOT NULL,
    stop_reason TEXT,
    episode_id TEXT,
    model_called INTEGER NOT NULL DEFAULT 0,
    broker_settled INTEGER NOT NULL DEFAULT 1,
    effective_model TEXT,
    validated_output_json TEXT,
    input_tokens INTEGER,
    output_tokens INTEGER,
    wall_seconds REAL,
    cost REAL,
    policy_version TEXT NOT NULL,
    reframe_policy TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_incubation_plan_status
    ON incubation_plans(status, updated_at);

CREATE TABLE IF NOT EXISTS incubation_wakes (
    dedupe_key TEXT PRIMARY KEY,
    observation_id TEXT NOT NULL,
    observation_fingerprint TEXT NOT NULL,
    plan_id TEXT NOT NULL,
    cycle INTEGER NOT NULL,
    wake_json TEXT NOT NULL,
    consumed_at TEXT NOT NULL,
    UNIQUE(plan_id, cycle)
);
CREATE INDEX IF NOT EXISTS idx_incubation_wake_observation
    ON incubation_wakes(observation_id);

CREATE TABLE IF NOT EXISTS incubation_command_ids (
    command_id TEXT PRIMARY KEY,
    command_kind TEXT NOT NULL,
    fingerprint TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS incubation_candidates (
    candidate_id TEXT PRIMARY KEY,
    plan_id TEXT NOT NULL,
    cycle INTEGER NOT NULL,
    proposal TEXT NOT NULL,
    source_refs_json TEXT NOT NULL,
    new_points_json TEXT NOT NULL,
    verification TEXT NOT NULL,
    uncertainty TEXT NOT NULL,
    runtime TEXT NOT NULL,
    effective_model TEXT NOT NULL,
    tier TEXT NOT NULL,
    policy_version TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    shown_at TEXT,
    outcome_at TEXT,
    outcome_reason TEXT,
    expires_at TEXT,
    cleaned_at TEXT,
    UNIQUE(plan_id, cycle)
);

CREATE TABLE IF NOT EXISTS incubation_control_commands (
    command_id TEXT PRIMARY KEY,
    fingerprint TEXT NOT NULL,
    plan_id TEXT NOT NULL,
    command_kind TEXT NOT NULL,
    result_json TEXT NOT NULL,
    occurred_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS incubation_outcome_commands (
    command_id TEXT PRIMARY KEY,
    fingerprint TEXT NOT NULL,
    candidate_id TEXT NOT NULL,
    outcome TEXT NOT NULL,
    reason TEXT,
    occurred_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS incubation_cleanup_commands (
    command_id TEXT PRIMARY KEY,
    fingerprint TEXT NOT NULL,
    before_at TEXT NOT NULL,
    result_json TEXT NOT NULL,
    occurred_at TEXT NOT NULL
);
"""


def migrate_v8_to_v9(conn) -> None:
    import sqlite3

    pending = ""
    for line in SCHEMA_SQL.splitlines(keepends=True):
        pending += line
        if sqlite3.complete_statement(pending):
            statement = pending.strip()
            if statement:
                conn.execute(statement)
            pending = ""
    conn.execute("UPDATE meta SET value='9' WHERE key='schema_version'")
