-- 冻结研讨参与者的可读连接身份，并保存每轮实际发生的工具活动摘要。
ALTER TABLE discussion_participants
ADD COLUMN connection_name TEXT;

ALTER TABLE discussion_participants
ADD COLUMN effective_model TEXT;

ALTER TABLE discussion_round_participants
ADD COLUMN activity_json TEXT;

ALTER TABLE discussion_attempts
ADD COLUMN activity_json TEXT;
