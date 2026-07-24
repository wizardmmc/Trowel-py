"""卡片解释版本的追加写入与时间线查询。"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass


@dataclass(frozen=True)
class ExplanationHistoryEntry:
    """`source` 由数据库约束；仓储写入时忽略传入的 `created_at`。"""

    id: str
    card_id: str
    explanation: str
    source: str
    created_at: str


def _entry_from_row(row: sqlite3.Row) -> ExplanationHistoryEntry:
    return ExplanationHistoryEntry(**dict(row))


def create_explanation_history_repository(
    conn: sqlite3.Connection,
) -> ExplanationHistoryRepository:
    return ExplanationHistoryRepository(conn)


class ExplanationHistoryRepository:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def find_by_card_id(self, card_id: str) -> list[ExplanationHistoryEntry]:
        """按数据库时间升序返回解释版本。"""
        rows = self.conn.execute(
            "select id, card_id, explanation, source, created_at "
            "from card_explanation_history "
            "where card_id = ? order by created_at asc",
            (card_id,),
        ).fetchall()
        return [_entry_from_row(row) for row in rows]

    def find_latest(self, card_id: str) -> ExplanationHistoryEntry | None:
        row = self.conn.execute(
            "select id, card_id, explanation, source, created_at "
            "from card_explanation_history "
            "where card_id = ? order by created_at desc limit 1",
            (card_id,),
        ).fetchone()
        if row is None:
            return None
        return _entry_from_row(row)

    def create(self, entry: ExplanationHistoryEntry) -> ExplanationHistoryEntry:
        """追加解释版本；`created_at` 始终使用数据库默认值。"""
        cursor = self.conn.execute(
            "insert into card_explanation_history (id, card_id, explanation, source) "
            "values (?, ?, ?, ?)",
            (entry.id, entry.card_id, entry.explanation, entry.source),
        )
        if cursor.rowcount != 1:
            raise RuntimeError(
                f"explanation history insert affected {cursor.rowcount} rows, expected 1"
            )
        return entry
