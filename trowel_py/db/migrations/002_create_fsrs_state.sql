CREATE TABLE fsrs_state(
    card_id TEXT PRIMARY KEY REFERENCES cards(id) ON DELETE CASCADE, -- auto delete related data when the referenced card is deleted
    stability REAL DEFAULT 0, -- memory stability
    difficulty REAL DEFAULT 0,
    elapsed_days INTEGER DEFAULT 0, -- days since last review
    scheduled_days INTEGER DEFAULT 0, -- days until next review
    reps INTEGER DEFAULT 0, -- number of reviews
    lapses INTEGER DEFAULT 0, -- times user chose the forget button
    state INTEGER DEFAULT 0 CHECK(state BETWEEN 0 and 3), -- 0:new - 1:learning - 2:review - 3:relearning
    due TEXT DEFAULT (datetime('now')), -- next review time
    last_review TEXT
);

CREATE INDEX idx_fsrs_state_due on fsrs_state(due);

CREATE TABLE review_logs(
    id TEXT PRIMARY KEY,
    card_id TEXT NOT NULL REFERENCES cards(id) ON DELETE CASCADE,
    rating INTEGER NOT NULL CHECK(rating BETWEEN 1 and 4), -- 1:again, 2:hard, 3:good, 4:easy
    state INTEGER not null,
    elapsed_days INTEGER DEFAULT 0,
    scheduled_days INTEGER DEFAULT 0,
    duration_ms INTEGER,
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE INDEX idx_review_log_card_id on review_logs(card_id);

CREATE TABLE card_explanation_history(
    id TEXT PRIMARY KEY,
    card_id TEXT NOT NULL REFERENCES cards(id) ON DELETE CASCADE, -- foreign key card table's id field
    explanation TEXT NOT NULL,
    source TEXT DEFAULT 'original' CHECK(source in ('original', 'llm', 'user')),
    created_at TEXT DEFAULT (datetime('now'))
);
