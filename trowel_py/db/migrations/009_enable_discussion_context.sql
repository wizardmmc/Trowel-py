-- 研讨参与者分别冻结 Memory、Profile 与 Self 注入条件。
-- 既有记录保持旧行为；只有新创建的 participant 按请求启用。
ALTER TABLE discussion_participants
    ADD COLUMN memory_enabled INTEGER NOT NULL DEFAULT 0
        CHECK (memory_enabled IN (0, 1));

ALTER TABLE discussion_participants
    ADD COLUMN profile_enabled INTEGER NOT NULL DEFAULT 0
        CHECK (profile_enabled IN (0, 1));

ALTER TABLE discussion_participants
    ADD COLUMN self_enabled INTEGER NOT NULL DEFAULT 0
        CHECK (self_enabled IN (0, 1));
