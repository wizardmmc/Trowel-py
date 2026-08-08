-- 参与者权限和 runtime/model 一样属于创建后不可变的会话条件。
ALTER TABLE discussion_participants
ADD COLUMN permission_mode TEXT;

ALTER TABLE discussion_participants
ADD COLUMN permission_preset TEXT
CHECK (
    permission_preset IS NULL
    OR permission_preset IN (
        'follow', 'read-only', 'workspace-write', 'danger-full-access'
    )
);

-- 旧研讨沿用变更前的实际启动行为，恢复时不会静默扩大权限。
UPDATE discussion_participants
SET permission_mode = 'dontAsk'
WHERE runtime = 'claude_code';

UPDATE discussion_participants
SET permission_preset = 'read-only'
WHERE runtime = 'codex';
