CREATE TABLE if not exists cards(
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    category TEXT NOT NULL,
    explanation TEXT NOT NULL,
    example TEXT,
    difficulty INTEGER DEFAULT 3 CHECK (difficulty BETWEEN 1 AND 5),
    source TEXT,
    tags TEXT,
    status TEXT DEFAULT 'active' CHECK (status IN ('active', 'archived', 'draft')),
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now'))
);

CREATE INDEX if not exists idx_cards_category ON cards(category); -- 支持按分类筛选卡片。
CREATE INDEX if not exists idx_cards_status ON cards(status); -- 支持按状态筛选卡片。


CREATE VIRTUAL TABLE cards_fts USING fts5(
    title, explanation, tags,
    content=cards, -- FTS5 外部内容表引用 cards，不重复存储原文。
    content_rowid=rowid
);

-- 外部内容表不会自动同步，写入 cards 时必须维护 FTS5 索引。
CREATE TRIGGER if not exists cards_fts_ai AFTER INSERT ON cards BEGIN
    INSERT INTO cards_fts(rowid, title, explanation, tags)
    VALUES (new.rowid, new.title, new.explanation, new.tags);
END;

-- FTS5 通过插入 `delete` 标记删除旧索引项。
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
