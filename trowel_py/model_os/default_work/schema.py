"""Default work 的 Model OS SQLite 扩展。"""

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS default_generations (
    generation_id TEXT PRIMARY KEY,
    identity_hash TEXT NOT NULL,
    work_item_id TEXT NOT NULL,
    episode_id TEXT,
    runtime TEXT NOT NULL,
    requested_tier TEXT NOT NULL,
    effective_model TEXT,
    policy_version TEXT NOT NULL,
    sources_json TEXT NOT NULL,
    status TEXT NOT NULL,
    model_called INTEGER NOT NULL DEFAULT 0,
    failure_reason TEXT,
    input_tokens INTEGER,
    output_tokens INTEGER,
    wall_seconds REAL,
    cost REAL,
    created_at TEXT NOT NULL,
    completed_at TEXT,
    validated_output_json TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_default_generation_live_identity
    ON default_generations(identity_hash)
    WHERE status IN ('pending', 'terminal', 'succeeded');

CREATE TABLE IF NOT EXISTS default_commands (
    command_id TEXT PRIMARY KEY NOT NULL CHECK(length(trim(command_id)) > 0),
    fingerprint TEXT NOT NULL,
    generation_id TEXT NOT NULL,
    runtime TEXT NOT NULL,
    source_refs_json TEXT NOT NULL,
    occurred_at TEXT NOT NULL,
    result_json TEXT
);

CREATE TABLE IF NOT EXISTS default_candidates (
    candidate_id TEXT PRIMARY KEY,
    generation_id TEXT NOT NULL,
    content TEXT NOT NULL,
    source_refs_json TEXT NOT NULL,
    related_question TEXT NOT NULL,
    why_useful TEXT NOT NULL,
    verification TEXT NOT NULL,
    uncertainty TEXT NOT NULL,
    runtime TEXT NOT NULL,
    effective_model TEXT NOT NULL,
    tier TEXT NOT NULL,
    policy_version TEXT NOT NULL,
    source_hashes_json TEXT NOT NULL,
    normalized_claim_hash TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    shown_at TEXT,
    outcome_at TEXT,
    outcome_reason TEXT,
    expires_at TEXT,
    UNIQUE(policy_version, source_hashes_json, normalized_claim_hash)
);

CREATE TABLE IF NOT EXISTS default_duplicate_outcomes (
    duplicate_id TEXT PRIMARY KEY,
    generation_id TEXT NOT NULL,
    existing_candidate_id TEXT NOT NULL,
    normalized_claim_hash TEXT NOT NULL,
    occurred_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS default_outcome_commands (
    command_id TEXT PRIMARY KEY NOT NULL CHECK(length(trim(command_id)) > 0),
    fingerprint TEXT NOT NULL,
    candidate_id TEXT NOT NULL,
    outcome TEXT NOT NULL,
    reason TEXT,
    occurred_at TEXT NOT NULL
);
"""


def migrate_v7_to_v8(conn) -> None:
    pending = ""
    import sqlite3

    for line in SCHEMA_SQL.splitlines(keepends=True):
        pending += line
        if sqlite3.complete_statement(pending):
            statement = pending.strip()
            if statement:
                conn.execute(statement)
            pending = ""
    conn.execute("UPDATE meta SET value='8' WHERE key='schema_version'")
