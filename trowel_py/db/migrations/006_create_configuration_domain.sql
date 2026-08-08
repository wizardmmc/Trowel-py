CREATE TABLE configuration_connections (
    id TEXT PRIMARY KEY,
    version INTEGER NOT NULL CHECK (version >= 1),
    identity_version INTEGER NOT NULL CHECK (identity_version >= 1),
    name TEXT NOT NULL,
    runtime TEXT NOT NULL CHECK (runtime IN ('claude_code', 'codex', 'direct_api')),
    kind TEXT NOT NULL CHECK (kind IN ('claude_compatible', 'codex_official', 'codex_custom', 'direct_api')),
    protocol TEXT NOT NULL CHECK (protocol IN ('anthropic_messages', 'openai_responses', 'codex_official')),
    base_url TEXT,
    models_url TEXT,
    auth_kind TEXT NOT NULL CHECK (auth_kind IN ('api_key', 'oauth_reference')),
    login_directory TEXT,
    proxy_url TEXT,
    proxy_username TEXT,
    claude_role_models TEXT NOT NULL DEFAULT '{}',
    codex_catalog TEXT NOT NULL DEFAULT '[]',
    catalog_request_identity TEXT,
    catalog_status TEXT NOT NULL DEFAULT 'idle' CHECK (catalog_status IN ('idle', 'ready', 'error', 'stale')),
    catalog_error_code TEXT,
    validation_status TEXT NOT NULL DEFAULT 'unknown' CHECK (validation_status IN ('verified', 'invalid', 'unknown', 'stale')),
    capability_version TEXT NOT NULL,
    last_session_choice TEXT,
    deleted_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX idx_configuration_connections_runtime
    ON configuration_connections(runtime, deleted_at, name);

CREATE TABLE configuration_secrets (
    connection_id TEXT NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('api_key', 'proxy_password')),
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (connection_id, kind),
    FOREIGN KEY (connection_id) REFERENCES configuration_connections(id) ON DELETE CASCADE
);

CREATE TABLE configuration_secret_versions (
    connection_id TEXT NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('api_key', 'proxy_password')),
    version INTEGER NOT NULL CHECK (version >= 1),
    updated_at TEXT NOT NULL,
    PRIMARY KEY (connection_id, kind),
    FOREIGN KEY (connection_id) REFERENCES configuration_connections(id) ON DELETE CASCADE
);

CREATE TABLE configuration_model_catalogs (
    request_identity TEXT PRIMARY KEY,
    connection_id TEXT NOT NULL,
    models TEXT NOT NULL,
    source_endpoint TEXT NOT NULL,
    fetched_at TEXT NOT NULL,
    FOREIGN KEY (connection_id) REFERENCES configuration_connections(id) ON DELETE CASCADE
);

CREATE INDEX idx_configuration_model_catalogs_connection
    ON configuration_model_catalogs(connection_id, fetched_at DESC);

CREATE TABLE configuration_session_configs (
    id TEXT PRIMARY KEY,
    version INTEGER NOT NULL CHECK (version >= 1),
    name TEXT NOT NULL,
    runtime TEXT NOT NULL CHECK (runtime IN ('claude_code', 'codex', 'direct_api')),
    connection_id TEXT NOT NULL,
    connection_identity_version INTEGER NOT NULL CHECK (connection_identity_version >= 1),
    model TEXT NOT NULL,
    effort TEXT,
    capability_version TEXT NOT NULL,
    deleted_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (connection_id) REFERENCES configuration_connections(id)
);

CREATE INDEX idx_configuration_session_configs_connection
    ON configuration_session_configs(connection_id, deleted_at, name);

CREATE TABLE configuration_task_bindings (
    task_id TEXT PRIMARY KEY CHECK (task_id IN ('memory_refine', 'profile_distill', 'memory_daily', 'memory_weekly', 'memory_monthly')),
    version INTEGER NOT NULL CHECK (version >= 1),
    session_configuration_id TEXT,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (session_configuration_id) REFERENCES configuration_session_configs(id)
);

CREATE TABLE configuration_agent_defaults (
    id TEXT PRIMARY KEY CHECK (id = 'default'),
    version INTEGER NOT NULL CHECK (version >= 1),
    session_configuration_id TEXT,
    permission TEXT,
    memory_enabled INTEGER NOT NULL CHECK (memory_enabled IN (0, 1)),
    profile_enabled INTEGER NOT NULL CHECK (profile_enabled IN (0, 1)),
    self_enabled INTEGER NOT NULL CHECK (self_enabled IN (0, 1)),
    updated_at TEXT NOT NULL,
    FOREIGN KEY (session_configuration_id) REFERENCES configuration_session_configs(id)
);

CREATE TABLE configuration_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
