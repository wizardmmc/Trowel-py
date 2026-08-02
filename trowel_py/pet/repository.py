"""读写默认玩家的宠物状态。"""

import sqlite3
from datetime import datetime

from trowel_py.pet.models import Pet
from trowel_py.player.repository import create_player_repository


def create_pet_repository(conn: sqlite3.Connection):
    """创建宠物数据仓库并确保默认宠物已经存在。

    预建默认宠物，避免后续更新在缺行时静默失效。
    """
    repo = PetRepository(conn)
    repo.find_or_create()
    return repo


class PetRepository:
    """负责默认宠物状态的数据库读写。"""

    def __init__(self, conn: sqlite3.Connection) -> None:
        """保存宠物数据使用的数据库连接。"""
        self.conn = conn

    def find_or_create(self) -> Pet:
        """原子创建并返回默认宠物，同时保证外键引用的默认玩家存在。"""
        create_player_repository(self.conn).find_or_create()
        self.conn.execute(
            "insert into pets (player_id) values (?) "
            "on conflict(player_id) do nothing",
            ("default",),
        )
        row = self.conn.execute(
            "select * from pets where player_id = ?", ("default",)
        ).fetchone()
        return self._row_to_pet(row)

    def update_mood(self, mood: str) -> None:
        """更新默认宠物的心情和修改时间。"""
        self.conn.execute(
            "update pets set mood = ?, updated_at = ? where player_id = 'default'",
            (mood, datetime.now().isoformat()),
        )

    def update_hunger(self, hunger: int) -> None:
        """更新默认宠物的饱食度和修改时间。"""
        self.conn.execute(
            "update pets set hunger = ?, updated_at = ? where player_id = 'default'",
            (hunger, datetime.now().isoformat()),
        )

    def update_equipped_hat(self, hat_row_id: str | None) -> None:
        """更新默认宠物引用的帽子库存行。"""
        self.conn.execute(
            "update pets set equipped_hat = ?, updated_at = ? where player_id = 'default'",
            (hat_row_id, datetime.now().isoformat()),
        )

    def _row_to_pet(self, row: sqlite3.Row) -> Pet:
        """将数据库行还原为宠物状态。"""
        row_dict = dict(row)
        row_dict["updated_at"] = datetime.fromisoformat(row_dict["updated_at"])
        return Pet(**row_dict)
