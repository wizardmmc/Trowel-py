import sqlite3
from datetime import datetime

from trowel_py.schemas.pet import Pet


def create_pet_repository(conn: sqlite3.Connection):
    """预建 singleton，避免后续更新在缺行时静默失效。"""
    repo = PetRepository(conn)
    repo.find_or_create()
    return repo


class PetRepository:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def find_or_create(self) -> Pet:
        row = self.conn.execute(
            "select * from pets where player_id = ?", ("default",)
        ).fetchone()
        if row is None:
            self.conn.execute("insert into pets (player_id) values (?)", ("default",))
            row = self.conn.execute(
                "select * from pets where player_id = ?", ("default",)
            ).fetchone()
        return self._row_to_pet(row)

    def update_mood(self, mood: str) -> None:
        self.conn.execute(
            "update pets set mood = ?, updated_at = ? where player_id = 'default'",
            (mood, datetime.now().isoformat()),
        )

    def update_hunger(self, hunger: int) -> None:
        self.conn.execute(
            "update pets set hunger = ?, updated_at = ? where player_id = 'default'",
            (hunger, datetime.now().isoformat()),
        )

    def update_equipped_hat(self, hat_row_id: str | None) -> None:
        self.conn.execute(
            "update pets set equipped_hat = ?, updated_at = ? where player_id = 'default'",
            (hat_row_id, datetime.now().isoformat()),
        )

    def _row_to_pet(self, row: sqlite3.Row) -> Pet:
        row_dict = dict(row)
        row_dict["updated_at"] = datetime.fromisoformat(row_dict["updated_at"])
        return Pet(**row_dict)
