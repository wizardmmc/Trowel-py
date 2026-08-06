-- 旧版把 Codex 上游完整模型列表当成 Agent 候选。升级后要求用户显式刷新，
-- 由 app-server 原生目录过滤 image 等非交互模型，再保留用户选择的有序白名单。
UPDATE configuration_connections
SET version = version + 1,
    catalog_request_identity = NULL,
    catalog_status = 'stale',
    catalog_error_code = NULL,
    validation_status = 'stale',
    updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
WHERE runtime = 'codex'
  AND kind = 'codex_custom'
  AND deleted_at IS NULL
  AND catalog_status = 'ready';
