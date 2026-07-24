CREATE TABLE if not exists fsrs_state(
    card_id TEXT PRIMARY KEY REFERENCES cards(id) ON DELETE CASCADE,
    stability REAL DEFAULT 0,
    difficulty REAL DEFAULT 0,
    elapsed_days INTEGER DEFAULT 0,
    scheduled_days INTEGER DEFAULT 0,
    reps INTEGER DEFAULT 0,
    lapses INTEGER DEFAULT 0,
    state INTEGER DEFAULT 0 CHECK(state BETWEEN 0 and 3), -- FSRS 状态：0=New，1=Learning，2=Review，3=Relearning。
    due TEXT DEFAULT (datetime('now')),
    last_review TEXT
);

CREATE INDEX if not exists idx_fsrs_state_due on fsrs_state(due);

CREATE TABLE if not exists review_logs(
    id TEXT PRIMARY KEY,
    card_id TEXT NOT NULL REFERENCES cards(id) ON DELETE CASCADE,
    rating INTEGER NOT NULL CHECK(rating BETWEEN 1 and 4), -- 评分值：1=Again，2=Hard，3=Good，4=Easy。
    state INTEGER not null,
    elapsed_days INTEGER DEFAULT 0,
    scheduled_days INTEGER DEFAULT 0,
    duration_ms INTEGER,
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE INDEX if not exists idx_review_log_card_id on review_logs(card_id);

CREATE TABLE if not exists card_explanation_history(
    id TEXT PRIMARY KEY,
    card_id TEXT NOT NULL REFERENCES cards(id) ON DELETE CASCADE,
    explanation TEXT NOT NULL,
    source TEXT DEFAULT 'original' CHECK(source in ('original', 'llm', 'user')),
    created_at TEXT DEFAULT (datetime('now'))
);
