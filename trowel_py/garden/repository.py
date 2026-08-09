"""查询花园展示所需的卡片、复习状态和统计。"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from typing import Any


def create_garden_repository(conn: sqlite3.Connection) -> GardenRepository:
    """用指定数据库连接创建花园数据仓库。"""
    return GardenRepository(conn)


class GardenRepository:
    """负责花园植物和统计数据的数据库查询。"""

    def __init__(self, conn: sqlite3.Connection) -> None:
        """保存花园查询使用的数据库连接。"""
        self.conn = conn

    def get_all_plants(self) -> list[dict[str, Any]]:
        """联表返回所有卡片及其复习状态。

        保留尚无复习状态的卡片，其状态字段返回 `None`。
        """
        rows = self.conn.execute(
            "select c.id, c.title, c.category, c.explanation, "
            "s.state, s.stability, s.reps, s.due "
            "from cards c left join fsrs_state s on c.id = s.card_id"
        ).fetchall()
        return [dict(row) for row in rows]

    def get_stats(self) -> dict[str, int | float]:
        """统计植物总数、到期数和开花率。"""
        now = datetime.now(timezone.utc).isoformat()
        row = self.conn.execute(
            "select count(*) as total_plants, "
            "sum(case when s.due <= ? then 1 else 0 end) as due_count, "
            "sum(case when s.state = 2 then 1 else 0 end) as flowering_count "
            "from cards c left join fsrs_state s on c.id = s.card_id",
            (now,),
        ).fetchone()
        total = row["total_plants"] or 0
        due_count = row["due_count"] or 0
        flowering_count = row["flowering_count"] or 0
        flowering_rate = round((flowering_count / total) * 100, 1) if total > 0 else 0.0
        return {
            "total_plants": total,
            "due_count": due_count,
            "flowering_rate": flowering_rate,
        }
