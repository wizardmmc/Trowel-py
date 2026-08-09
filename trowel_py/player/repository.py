"""读写默认玩家及其库存。"""

from __future__ import annotations

import sqlite3
import uuid
from datetime import datetime

from trowel_py.player.models import InventoryItem, Player


def _player_from_row(row: sqlite3.Row) -> Player:
    """将数据库行还原为玩家状态。"""
    data = dict(row)
    data["last_active"] = datetime.fromisoformat(data["last_active"])
    data["created_at"] = datetime.fromisoformat(data["created_at"])
    return Player(**data)


def _inventory_item_from_row(row: sqlite3.Row) -> InventoryItem:
    """将数据库行还原为库存物品。"""
    data = dict(row)
    data["obtained_at"] = datetime.fromisoformat(data["obtained_at"])
    return InventoryItem(**data)


def create_player_repository(conn: sqlite3.Connection) -> PlayerRepository:
    """用指定数据库连接创建玩家数据仓库。"""
    return PlayerRepository(conn)


class PlayerRepository:
    """负责默认玩家和库存的数据库读写。"""

    def __init__(self, conn: sqlite3.Connection) -> None:
        """保存玩家数据使用的数据库连接。"""
        self.conn = conn

    def find_or_create(self) -> Player:
        """原子创建并返回默认玩家，允许多个请求同时访问全新数据库。"""
        self.conn.execute(
            "insert into players (id, last_active) values (?, ?) "
            "on conflict(id) do nothing",
            ("default", datetime.now().isoformat()),
        )
        row = self.conn.execute(
            "select * from players where id = ?", ("default",)
        ).fetchone()
        return _player_from_row(row)

    def update_xp(self, delta: int) -> None:
        """按增量修改默认玩家的经验。"""
        self.conn.execute(
            "update players set xp = xp + ? where id = 'default'", (delta,)
        )

    def update_coins(self, delta: int) -> None:
        """按增量修改默认玩家的金币。"""
        self.conn.execute(
            "update players set coins = coins + ? where id = 'default'", (delta,)
        )

    def update_streak(self, streak_days: int, last_active: datetime) -> None:
        """保存默认玩家的连续天数和最近活跃时间。"""
        self.conn.execute(
            "update players set streak_days = ?, last_active = ? where id = 'default'",
            (streak_days, last_active.isoformat()),
        )

    def find_inventory(self) -> list[InventoryItem]:
        """返回默认玩家的全部库存行。"""
        rows = self.conn.execute(
            "select * from inventory where player_id = 'default'"
        ).fetchall()
        return [_inventory_item_from_row(row) for row in rows]

    def add_item(self, item_id: str, item_type: str) -> None:
        """为默认玩家新增一条库存记录。"""
        row_id = uuid.uuid4().hex[:12]
        self.conn.execute(
            "insert into inventory "
            "(id, player_id, item_id, item_type) values (?, ?, ?, ?)",
            (row_id, "default", item_id, item_type),
        )

    def remove_item(self, id: str) -> None:
        """按库存行 ID 删除一件物品。"""
        self.conn.execute("delete from inventory where id = ?", (id,))

    def find_item_by_id(self, id: str) -> InventoryItem | None:
        """按库存行 ID 查找默认玩家的一件物品。"""
        row = self.conn.execute(
            "select * from inventory where id = ? and player_id = 'default'",
            (id,),
        ).fetchone()
        if row is None:
            return None
        return _inventory_item_from_row(row)

    def set_equipped(self, id: str, equipped: int) -> None:
        """设置一条库存记录的装备状态。"""
        self.conn.execute(
            "update inventory set equipped = ? where id = ?", (equipped, id)
        )

    def unequip_all_hats(self) -> None:
        """清除默认玩家的全部帽子装备状态，修复遗留的多帽并存数据。"""
        self.conn.execute(
            "update inventory set equipped = 0 "
            "where player_id = 'default' and item_type = 'hat'"
        )
