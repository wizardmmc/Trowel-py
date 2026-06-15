import sqlite3
import uuid
from datetime import datetime
from trowel_py.schemas.player import Player, InventoryItem


def create_player_repository(conn: sqlite3.Connection):
    """
    build a PlayerRepository bound to the given connection.
    """
    return PlayerRepository(conn)


class PlayerRepository:
    """
    data access for the single default player and their inventory.
    """

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def find_or_create(self) -> Player:
        """
        player must exist
        """
        row = self.conn.execute(
            "select * from players where id = ?", ("default", )
        ).fetchone()
        if row is None:
            self.conn.execute(
                "insert into players (last_active) values (?)", (datetime.now().isoformat(), )
            )
            row = self.conn.execute(
                "select * from players where id = ?", ("default", )
            ).fetchone()
        row_dict = dict(row)
        row_dict["last_active"] = datetime.fromisoformat(row_dict["last_active"])
        row_dict["created_at"] = datetime.fromisoformat(row_dict["created_at"])
        return Player(**row_dict)

    def update_xp(self, delta: int) -> None:
        """
        update xp, calculate in db

        Args:
            delta: amount to add to xp (negative subtracts).
        """
        self.conn.execute(
            "update players set xp = xp + ? where id = 'default'", (delta, )
        )

    def update_coins(self, delta: int) -> None:
        """
        update coin, calculate in db

        Args:
            delta: amount to add to coins (negative spends).
        """
        self.conn.execute(
            "update players set coins = coins + ? where id = 'default'", (delta, )
        )

    def update_streak(self, streak_days: int, last_active: datetime) -> None:
        """
        update streak days, calculate in db

        Args:
            streak_days: the new streak count (computed by the service).
            last_active: the timestamp to record as most recent activity.
        """
        self.conn.execute(
            "update players set streak_days = ?, last_active = ? where id = 'default'",
            (streak_days, last_active.isoformat(), )
        )

    def find_inventory(self) -> list[InventoryItem]:
        """
        find by external key
        """
        rows = self.conn.execute(
            "select * from inventory where player_id = 'default'"
        ).fetchall()
        res = []
        for row in rows:
            row_dict = dict(row)
            row_dict["obtained_at"] = datetime.fromisoformat(row_dict["obtained_at"])
            res.append(InventoryItem(**row_dict))
        return res

    def add_item(self, item_id: str, item_type: str) -> None:
        """
        insert a new item into the default player's inventory.

        Args:
            item_id: catalog id, e.g. 'food_basic', 'hat_straw'.
            item_type: 'hat' or 'food'.
        """
        id = uuid.uuid4().hex[:12]
        self.conn.execute(
            "insert into inventory (id, player_id, item_id, item_type) values (?, ?, ?, ?)",
            (id, "default", item_id, item_type, )
        )

    def remove_item(self, id: str) -> None:
        """
        remove item by id

        Args:
            id: the inventory row id to delete.
        """
        self.conn.execute(
            "delete from inventory where id = ?", (id, )
        )
