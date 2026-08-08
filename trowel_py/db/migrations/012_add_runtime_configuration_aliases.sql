ALTER TABLE configuration_connections
    ADD COLUMN claude_auto_memory_disabled INTEGER NOT NULL DEFAULT 0
    CHECK (claude_auto_memory_disabled IN (0, 1));

ALTER TABLE configuration_session_configs
    ADD COLUMN identity_version INTEGER NOT NULL DEFAULT 1
    CHECK (identity_version >= 1);

ALTER TABLE configuration_session_configs
    ADD COLUMN stable_alias TEXT;

ALTER TABLE configuration_session_configs
    ADD COLUMN agent_callable INTEGER NOT NULL DEFAULT 0
    CHECK (agent_callable IN (0, 1));

CREATE TABLE configuration_session_aliases (
    alias TEXT PRIMARY KEY,
    session_configuration_id TEXT NOT NULL,
    is_current INTEGER NOT NULL CHECK (is_current IN (0, 1)),
    created_at TEXT NOT NULL,
    retired_at TEXT,
    FOREIGN KEY (session_configuration_id)
        REFERENCES configuration_session_configs(id)
);

CREATE UNIQUE INDEX idx_configuration_session_aliases_current
    ON configuration_session_aliases(session_configuration_id)
    WHERE is_current = 1;
