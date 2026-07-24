import sqlite3
import uuid
from datetime import datetime

from trowel_py.player.models import InventoryItem, Player


def _player_from_row(row: sqlite3.Row) -> Player:
    data = dict(row)
    data["last_active"] = datetime.fromisoformat(data["last_active"])
    data["created_at"] = datetime.fromisoformat(data["created_at"])
    return Player(**data)


def _inventory_item_from_row(row: sqlite3.Row) -> InventoryItem:
    data = dict(row)
    data["obtained_at"] = datetime.fromisoformat(data["obtained_at"])
    return InventoryItem(**data)


def create_player_repository(conn: sqlite3.Connection):
    return PlayerRepository(conn)


class PlayerRepository:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def find_or_create(self) -> Player:
        row = self.conn.execute(
            "select * from players where id = ?", ("default",)
        ).fetchone()
        if row is None:
            self.conn.execute(
                "insert into players (last_active) values (?)",
                (datetime.now().isoformat(),),
            )
            row = self.conn.execute(
                "select * from players where id = ?", ("default",)
            ).fetchone()
        return _player_from_row(row)

    def update_xp(self, delta: int) -> None:
        self.conn.execute(
            "update players set xp = xp + ? where id = 'default'", (delta,)
        )

    def update_coins(self, delta: int) -> None:
        self.conn.execute(
            "update players set coins = coins + ? where id = 'default'", (delta,)
        )

    def update_streak(self, streak_days: int, last_active: datetime) -> None:
        self.conn.execute(
            "update players set streak_days = ?, last_active = ? where id = 'default'",
            (streak_days, last_active.isoformat()),
        )

    def find_inventory(self) -> list[InventoryItem]:
        rows = self.conn.execute(
            "select * from inventory where player_id = 'default'"
        ).fetchall()
        return [_inventory_item_from_row(row) for row in rows]

    def add_item(self, item_id: str, item_type: str) -> None:
        row_id = uuid.uuid4().hex[:12]
        self.conn.execute(
            "insert into inventory "
            "(id, player_id, item_id, item_type) values (?, ?, ?, ?)",
            (row_id, "default", item_id, item_type),
        )

    def remove_item(self, id: str) -> None:
        self.conn.execute("delete from inventory where id = ?", (id,))

    def find_item_by_id(self, id: str) -> InventoryItem | None:
        row = self.conn.execute(
            "select * from inventory where id = ? and player_id = 'default'",
            (id,),
        ).fetchone()
        if row is None:
            return None
        return _inventory_item_from_row(row)

    def set_equipped(self, id: str, equipped: int) -> None:
        self.conn.execute(
            "update inventory set equipped = ? where id = ?", (equipped, id)
        )

    def unequip_all_hats(self) -> None:
        """清除默认玩家的全部帽子装备状态，修复遗留的多帽并存数据。"""
        self.conn.execute(
            "update inventory set equipped = 0 "
            "where player_id = 'default' and item_type = 'hat'"
        )
