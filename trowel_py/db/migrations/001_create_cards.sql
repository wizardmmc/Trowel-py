CREATE TABLE if not exists cards(
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL, -- NOT NULL means this field is base for a card
    category TEXT NOT NULL,
    explanation TEXT NOT NULL,
    example TEXT,
    difficulty INTEGER DEFAULT 3 CHECK (difficulty BETWEEN 1 AND 5), -- CHECK is a method to ensure data quality
    source TEXT,
    tags TEXT,
    status TEXT DEFAULT 'active' CHECK (status IN ('active', 'archived', 'draft')), -- acts like an enum
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now'))
);

CREATE INDEX if not exists idx_cards_category ON cards(category); -- on business view, we usually search by category like 'find all cards about python'
CREATE INDEX if not exists idx_cards_status ON cards(status);  -- like 'find all cards that need to review'


-- create full text search for text-heavy fields
CREATE VIRTUAL TABLE cards_fts USING fts5(
    title, explanation, tags,
    content=cards, -- content table mode: FTS5 references cards table data, no duplicate storage
    content_rowid=rowid
);

-- set trigger, because FTS5 can't auto update when a new row is inserted into "cards" table
CREATE TRIGGER if not exists cards_fts_ai AFTER INSERT ON cards BEGIN
    INSERT INTO cards_fts(rowid, title, explanation, tags)
    VALUES (new.rowid, new.title, new.explanation, new.tags);
END;

-- FTS5 can only use INSERT syntax, so we insert a 'delete' marker to remove entries
CREATE TRIGGER if not exists cards_fts_ad AFTER DELETE ON cards BEGIN
    INSERT INTO cards_fts(cards_fts, rowid, title, explanation, tags)
    VALUES ('delete', old.rowid, old.title, old.explanation, old.tags);
END;

CREATE TRIGGER if not exists cards_fts_au AFTER UPDATE ON cards BEGIN
    INSERT INTO cards_fts(cards_fts, rowid, title, explanation, tags)
    VALUES ('delete', old.rowid, old.title, old.explanation, old.tags);
    INSERT INTO cards_fts(rowid, title, explanation, tags)
    VALUES (new.rowid, new.title, new.explanation, new.tags);
END;

