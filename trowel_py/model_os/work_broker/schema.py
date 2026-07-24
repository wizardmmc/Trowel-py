"""WorkBroker 独立 SQLite schema。"""

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS work_leases (
    lease_id TEXT PRIMARY KEY,
    slot TEXT NOT NULL,
    provider TEXT NOT NULL,
    account_id TEXT NOT NULL,
    work_kind TEXT NOT NULL,
    model_tier TEXT NOT NULL,
    task_id TEXT,
    work_item_id TEXT,
    -- JSON BudgetDimensions；NULL 表示无限额度（foreground / maintenance）。
    -- 读回时解析成 WorkLease.granted_cap。
    granted_cap TEXT,
    -- holding(0) vs running(1)：由 begin_call 在发起 native 调用前置 1，
    -- 这样已发出调用的 default 不再被抢占；从没 begin_call 的 lease 保持 holding、可被抢占。
    started INTEGER NOT NULL DEFAULT 0,
    -- maintenance 不可中断的写窗口（begin_critical_section）。
    in_critical INTEGER NOT NULL DEFAULT 0,
    acquired_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    fencing_token INTEGER NOT NULL,
    -- 仅审计用（记录是哪个请求 key 拿到的）；去重权威是 work_idempotency_keys（请求级，非槽级）。
    idempotency_key TEXT,
    policy_version TEXT NOT NULL,
    released_at TEXT
);

-- CAS 原语：每个槽至多一个活跃（未释放）持有者。对应 model_os.store 的 idx_leases_active。
CREATE UNIQUE INDEX IF NOT EXISTS idx_work_leases_active
    ON work_leases(slot) WHERE released_at IS NULL;

-- 每槽严格单调的 fencing 计数器。对应 model_os.store 的 lease_fence_counters。
CREATE TABLE IF NOT EXISTS work_fence_counters (
    slot TEXT PRIMARY KEY,
    last_token INTEGER NOT NULL
);

-- 请求级幂等：一个 key -> 一个跨所有槽的活跃 lease。
-- 槽是 broker 动态选的，按槽去重会让同一个请求在两个槽各拿一份；这里在请求级去重。
-- fingerprint 把 key 绑到请求形状（kind/provider/account/task/catchup），
-- 使「同 key 不同请求」被拒绝，而不是静默复用上次的 lease。
CREATE TABLE IF NOT EXISTS work_idempotency_keys (
    idempotency_key TEXT PRIMARY KEY,
    lease_id TEXT NOT NULL,
    fingerprint TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS work_usage (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    observation_id TEXT,
    lease_id TEXT,
    -- 归因维度在记账时从 LEASE 行拷贝，不信调用方（持有 GLM foreground 的不能记成 Codex 用量）。
    provider TEXT NOT NULL,
    account_id TEXT NOT NULL,
    work_kind TEXT NOT NULL,
    model_tier TEXT NOT NULL,
    task_id TEXT,
    work_item_id TEXT,
    calls INTEGER NOT NULL DEFAULT 0,
    input_tokens INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    cost REAL,
    wall_seconds INTEGER,
    occurred_at TEXT NOT NULL,
    day TEXT NOT NULL,
    policy_version TEXT NOT NULL
);

-- 单次观测幂等：同 `(lease_id, observation_id)` 的重复 record_usage 是空操作
-- （重试不重复记账）。作用域到 lease，使两个不同 lease 可复用同一 observation id。
CREATE UNIQUE INDEX IF NOT EXISTS idx_work_usage_obs
    ON work_usage(lease_id, observation_id) WHERE observation_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_work_usage_dim
    ON work_usage(work_kind, provider, account_id, day);

-- maintenance 补跑 claim：`(scope, period)` 在 grant 时 CLAIMED（靠主键原子去重），
-- 只有成功 `complete` 才 COMPLETED。lease 已死（完成前崩溃）的 claim 会自愈——
-- 下次请求清掉它重新 grant，必要维护不会永久丢失。
CREATE TABLE IF NOT EXISTS work_catchup_watermark (
    scope TEXT NOT NULL,
    period TEXT NOT NULL,
    work_kind TEXT NOT NULL,
    lease_id TEXT,
    state TEXT NOT NULL,
    claimed_at TEXT NOT NULL,
    completed_at TEXT,
    PRIMARY KEY (scope, period, work_kind)
);
"""
