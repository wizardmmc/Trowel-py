-- 多方研讨的控制面。讨论正文只保存在本表和 discussion artifact 中，
-- participant 原生会话只接收协调器构造的公开快照。
CREATE TABLE IF NOT EXISTS discussions (
    id TEXT PRIMARY KEY,
    create_request_id TEXT NOT NULL UNIQUE,
    create_request_hash TEXT NOT NULL,
    topic TEXT NOT NULL,
    workdir TEXT NOT NULL,
    progression_mode TEXT NOT NULL
        CHECK (progression_mode IN ('automatic', 'user_guided')),
    max_rounds INTEGER,
    status TEXT NOT NULL
        CHECK (status IN (
            'draft', 'running', 'waiting_user', 'completed', 'stopped',
            'needs_reconcile', 'deleted'
        )),
    version INTEGER NOT NULL DEFAULT 1 CHECK (version >= 1),
    active_round_number INTEGER,
    episode_id TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    completed_at TEXT,
    stopped_at TEXT,
    deleted_at TEXT,
    CHECK (
        (progression_mode = 'automatic' AND max_rounds IS NOT NULL AND max_rounds >= 1)
        OR (progression_mode = 'user_guided' AND max_rounds IS NULL)
    )
);

CREATE TABLE IF NOT EXISTS discussion_participants (
    id TEXT PRIMARY KEY,
    discussion_id TEXT NOT NULL REFERENCES discussions(id) ON DELETE CASCADE,
    position INTEGER NOT NULL CHECK (position >= 0),
    name TEXT NOT NULL,
    runtime TEXT NOT NULL CHECK (runtime IN ('claude_code', 'codex')),
    connection_id TEXT NOT NULL,
    model TEXT NOT NULL,
    effort TEXT,
    session_configuration_id TEXT,
    connection_identity_version INTEGER,
    owner_ref TEXT NOT NULL UNIQUE,
    agent_session_id TEXT,
    native_session_id TEXT,
    status TEXT NOT NULL
        CHECK (status IN ('pending', 'ready', 'needs_reconcile', 'closed')),
    capability_version TEXT,
    capability_source TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (discussion_id, position),
    UNIQUE (discussion_id, name)
);

CREATE TABLE IF NOT EXISTS discussion_user_messages (
    id TEXT PRIMARY KEY,
    discussion_id TEXT NOT NULL REFERENCES discussions(id) ON DELETE CASCADE,
    sequence INTEGER NOT NULL CHECK (sequence >= 1),
    after_round_number INTEGER NOT NULL CHECK (after_round_number >= 0),
    author_role TEXT NOT NULL CHECK (author_role = 'user'),
    target_scope TEXT NOT NULL CHECK (target_scope IN ('all', 'participant')),
    target_participant_id TEXT REFERENCES discussion_participants(id),
    body TEXT NOT NULL,
    message_artifact TEXT NOT NULL,
    message_sha256 TEXT NOT NULL,
    message_bytes INTEGER NOT NULL CHECK (message_bytes >= 0),
    profile_status TEXT NOT NULL DEFAULT 'pending'
        CHECK (profile_status IN ('pending', 'processed', 'ignored')),
    profile_source_id TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL,
    UNIQUE (discussion_id, sequence),
    CHECK (
        (target_scope = 'all' AND target_participant_id IS NULL)
        OR (target_scope = 'participant' AND target_participant_id IS NOT NULL)
    )
);

CREATE TABLE IF NOT EXISTS discussion_rounds (
    id TEXT PRIMARY KEY,
    discussion_id TEXT NOT NULL REFERENCES discussions(id) ON DELETE CASCADE,
    number INTEGER NOT NULL CHECK (number >= 1),
    kind TEXT NOT NULL CHECK (kind IN ('regular', 'final')),
    status TEXT NOT NULL
        CHECK (status IN ('sealed', 'running', 'published', 'stopped')),
    public_context_hash TEXT NOT NULL,
    input_artifact TEXT NOT NULL,
    input_bytes INTEGER NOT NULL CHECK (input_bytes >= 0),
    publication_artifact TEXT,
    publication_sha256 TEXT,
    publication_bytes INTEGER CHECK (publication_bytes IS NULL OR publication_bytes >= 0),
    started_at TEXT NOT NULL,
    published_at TEXT,
    stop_reason TEXT,
    UNIQUE (discussion_id, number)
);

CREATE TABLE IF NOT EXISTS discussion_round_participants (
    round_id TEXT NOT NULL REFERENCES discussion_rounds(id) ON DELETE CASCADE,
    participant_id TEXT NOT NULL REFERENCES discussion_participants(id),
    position INTEGER NOT NULL CHECK (position >= 0),
    status TEXT NOT NULL
        CHECK (status IN (
            'pending', 'running', 'succeeded', 'failed', 'limited',
            'timed_out', 'interrupted', 'cancelled', 'host_lost', 'needs_reconcile'
        )),
    current_attempt_id TEXT,
    output_artifact TEXT,
    output_sha256 TEXT,
    output_bytes INTEGER CHECK (output_bytes IS NULL OR output_bytes >= 0),
    error_code TEXT,
    error_message TEXT,
    usage_json TEXT,
    started_at TEXT,
    completed_at TEXT,
    PRIMARY KEY (round_id, participant_id),
    UNIQUE (round_id, position)
);

CREATE TABLE IF NOT EXISTS discussion_attempts (
    id TEXT PRIMARY KEY,
    round_id TEXT NOT NULL,
    participant_id TEXT NOT NULL,
    ordinal INTEGER NOT NULL CHECK (ordinal >= 1),
    status TEXT NOT NULL
        CHECK (status IN (
            'dispatching', 'running', 'succeeded', 'failed', 'limited', 'timed_out',
            'interrupted', 'cancelled', 'host_lost', 'needs_reconcile'
        )),
    dispatch_key TEXT NOT NULL UNIQUE,
    input_hash TEXT NOT NULL,
    event_artifact TEXT NOT NULL,
    event_sha256 TEXT,
    event_bytes INTEGER CHECK (event_bytes IS NULL OR event_bytes >= 0),
    output_artifact TEXT,
    output_sha256 TEXT,
    output_bytes INTEGER CHECK (output_bytes IS NULL OR output_bytes >= 0),
    root_turn_id TEXT,
    error_code TEXT,
    error_message TEXT,
    started_at TEXT NOT NULL,
    completed_at TEXT,
    FOREIGN KEY (round_id, participant_id)
        REFERENCES discussion_round_participants(round_id, participant_id)
        ON DELETE CASCADE,
    UNIQUE (round_id, participant_id, ordinal)
);

CREATE TABLE IF NOT EXISTS discussion_commands (
    discussion_id TEXT NOT NULL REFERENCES discussions(id) ON DELETE CASCADE,
    command_id TEXT NOT NULL,
    command_type TEXT NOT NULL,
    request_hash TEXT NOT NULL,
    result_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (discussion_id, command_id)
);

CREATE TABLE IF NOT EXISTS discussion_events (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    discussion_id TEXT NOT NULL REFERENCES discussions(id) ON DELETE CASCADE,
    event_type TEXT NOT NULL,
    version INTEGER NOT NULL,
    round_number INTEGER,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS discussion_promotions (
    id TEXT PRIMARY KEY,
    discussion_id TEXT NOT NULL REFERENCES discussions(id) ON DELETE CASCADE,
    kind TEXT NOT NULL CHECK (kind IN ('user_marked', 'agent_adopted', 'verified')),
    source_ref TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE (discussion_id, kind, source_ref)
);

-- artifact 完整性故障的 durable 对账 outbox。修复文件或 SQLite 引用后由启动扫描清除。
CREATE TABLE IF NOT EXISTS discussion_integrity_issues (
    discussion_id TEXT PRIMARY KEY REFERENCES discussions(id) ON DELETE CASCADE,
    issue_code TEXT NOT NULL,
    detected_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_discussions_status
    ON discussions(status, updated_at);
CREATE INDEX IF NOT EXISTS idx_discussion_participants_owner
    ON discussion_participants(owner_ref);
CREATE INDEX IF NOT EXISTS idx_discussion_messages_profile
    ON discussion_user_messages(profile_status, created_at);
CREATE INDEX IF NOT EXISTS idx_discussion_rounds_discussion
    ON discussion_rounds(discussion_id, number);
CREATE INDEX IF NOT EXISTS idx_discussion_events_replay
    ON discussion_events(discussion_id, sequence);
