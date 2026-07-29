"""保存并查询卡片解释的历史版本。"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass


@dataclass(frozen=True)
class ExplanationHistoryEntry:
    """记录一张已经保存的卡片的一个解释版本。

    Attributes:
        id: 解释版本的唯一 ID，由调用方提供。
        card_id: 这个解释版本所属的已保存卡片 ID。
        explanation: 该版本保存的解释正文。
        source: 解释来源，只能是 ``"original"``、``"llm"`` 或 ``"user"``。
        created_at: 数据库生成的保存时间；新建版本时传入的值不会写入数据库。
    """

    id: str
    card_id: str
    explanation: str
    source: str
    created_at: str


def _entry_from_row(row: sqlite3.Row) -> ExplanationHistoryEntry:
    """把包含全部解释历史字段的 SQLite 行转换为解释版本。"""

    return ExplanationHistoryEntry(**dict(row))


def create_explanation_history_repository(
    conn: sqlite3.Connection,
) -> ExplanationHistoryRepository:
    """创建使用给定数据库连接的解释历史仓储。

    ``conn`` 的事务和生命周期仍由调用方管理。
    """

    return ExplanationHistoryRepository(conn)


class ExplanationHistoryRepository:
    """使用 SQLite 追加和查询卡片的解释历史。"""

    def __init__(self, conn: sqlite3.Connection) -> None:
        """绑定由调用方负责提交、回滚和关闭的数据库连接。"""

        self.conn = conn

    def find_by_card_id(self, card_id: str) -> list[ExplanationHistoryEntry]:
        """按时间从旧到新读取全部解释版本；没有历史版本时返回空列表。"""
        rows = self.conn.execute(
            "select id, card_id, explanation, source, created_at "
            "from card_explanation_history "
            "where card_id = ? order by created_at asc",
            (card_id,),
        ).fetchall()
        return [_entry_from_row(row) for row in rows]

    def find_latest(self, card_id: str) -> ExplanationHistoryEntry | None:
        """读取一张卡片最新的解释版本；没有历史版本时返回 ``None``。"""

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
        """追加解释版本，并让数据库生成保存时间。

        ``entry.created_at`` 不会写入数据库。方法原样返回 ``entry``；如需数据库
        生成的保存时间，应重新查询该版本。

        Raises:
            sqlite3.IntegrityError: ``card_id`` 指向的卡片不存在、``id`` 已存在，
                或 ``source`` 不是允许的值。
            RuntimeError: 插入操作影响的记录数不是一行。
        """
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
