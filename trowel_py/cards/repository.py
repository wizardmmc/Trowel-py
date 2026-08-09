"""通过 SQLite 保存、查询、更新和全文搜索卡片。"""

from __future__ import annotations

import json
import sqlite3

from trowel_py.cards.models import Card


def _card_record(card: Card) -> dict[str, object]:
    """把卡片转换为 SQLite 字段，并序列化标签和两个时间值。"""

    data = card.model_dump()
    data["tags"] = json.dumps(data["tags"])
    data["created_at"] = data["created_at"].isoformat()
    data["updated_at"] = data["updated_at"].isoformat()
    return data


def _card_from_row(row: sqlite3.Row) -> Card:
    """反序列化 SQLite 行中的标签和时间值，得到卡片模型。"""

    data = dict(row)
    data["tags"] = json.loads(data["tags"])
    return Card(**data)


def create_card_repository(conn: sqlite3.Connection) -> CardRepository:
    """创建使用给定数据库连接的卡片仓储。

    ``conn`` 的事务和生命周期仍由调用方管理。
    """

    return CardRepository(conn)


class CardRepository:
    """使用 SQLite 读写卡片，并通过 FTS5 搜索标题、解释和标签。"""

    def __init__(self, conn: sqlite3.Connection) -> None:
        """绑定由调用方负责提交、回滚和关闭的数据库连接。"""

        self.conn = conn

    def create(self, card: Card) -> Card:
        """在当前事务中写入新卡片，并原样返回 ``card``。"""

        data = _card_record(card)
        self.conn.execute(
            "insert into cards "
            "(id, title, category, explanation, example, difficulty, source, "
            "tags, status, created_at, updated_at) "
            "values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                data["id"],
                data["title"],
                data["category"],
                data["explanation"],
                data["example"],
                data["difficulty"],
                data["source"],
                data["tags"],
                data["status"],
                data["created_at"],
                data["updated_at"],
            ),
        )
        return card

    def find_by_id(self, card_id: str) -> Card | None:
        """按卡片 ID 读取单张卡片；记录不存在时返回 ``None``。"""

        res = self.conn.execute(
            "select * from cards where id = (?)", (card_id,)
        ).fetchone()
        if res is None:
            return None
        return _card_from_row(res)

    def find_all(self) -> list[Card]:
        """读取数据库中的全部卡片；没有记录时返回空列表。"""

        rows = self.conn.execute("select * from cards").fetchall()
        return [_card_from_row(row) for row in rows]

    def update(self, card_id: str, new_card: Card) -> Card:
        """用 ``new_card`` 的内容更新 ``card_id`` 指定的卡片。

        ``new_card.id`` 不会写入数据库，方法会原样返回 ``new_card``。目标记录
        不存在时不会写入任何内容，也不会报错。
        """

        data = _card_record(new_card)
        self.conn.execute(
            "update cards set title=?, category=?, explanation=?, example=?, "
            "difficulty=?, source=?, tags=?, status=?, created_at=?, "
            "updated_at=? where id=?",
            (
                data["title"],
                data["category"],
                data["explanation"],
                data["example"],
                data["difficulty"],
                data["source"],
                data["tags"],
                data["status"],
                data["created_at"],
                data["updated_at"],
                card_id,
            ),
        )
        return new_card

    def search_by_fts5(self, query: str) -> list[Card]:
        """按 FTS5 查询表达式搜索标题、解释和标签；没有匹配时返回空列表。"""

        rows = self.conn.execute(
            "select * from cards where rowid in "
            "(select rowid from cards_fts where cards_fts match ?)",
            (query,),
        ).fetchall()
        return [_card_from_row(row) for row in rows]
