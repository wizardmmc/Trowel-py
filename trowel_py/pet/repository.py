import sqlite3
from datetime import datetime

from trowel_py.schemas.pet import Pet

def create_pet_repository(conn: sqlite3.Connection):
    """
    build a PetRepository bound to the given connection.

    also ensures the singleton pet row exists — every update_* method does a
    `where player_id='default'` that silently hits 0 rows if the row is absent,
    so we create it here once instead of relying on each caller.
    """
    repo = PetRepository(conn)
    repo.find_or_create()
    return repo


class PetRepository:
    """
    data access for the single default player's pet
    """
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def find_or_create(self) -> Pet:
        """
        the pet must exist - create it with defaults on first access
        """
        row = self.conn.execute(
            "select * from pets where player_id = ?", ("default",)
        ).fetchone()
        if row is None:
            self.conn.execute(
                "insert into pets (player_id) values (?)", ("default",)
            )
            row = self.conn.execute(
                "select * from pets where player_id = ?", ("default",)
            ).fetchone()
        return self._row_to_pet(row)
    
    def update_mood(self, mood: str) -> None:
        """
        overwrite the pet's mood.

        Args:
            mood: the new mood (one of PetMood).
        """
        self.conn.execute(
            "update pets set mood = ?, updated_at = ? where player_id = 'default'",
            (mood, datetime.now().isoformat()),
        )

    def update_hunger(self, hunger: int) -> None:
        """
        overwrite the pet's hunger.

        Args:
            hunger: the new hunger value (0-100); clamped by the table check constraint.
        """
        self.conn.execute(
            "update pets set hunger = ?, updated_at = ? where player_id = 'default'",
            (hunger, datetime.now().isoformat()),
        )

    def update_equipped_hat(self, hat_row_id: str | None) -> None:
        """
        set which hat the pet currently wears.

        Args:
            hat_row_id: the inventory row id of the equipped hat, or None to go bare-headed.
        """
        self.conn.execute(
            "update pets set equipped_hat = ?, updated_at = ? where player_id = 'default'",
            (hat_row_id, datetime.now().isoformat()),
        )
    
    def _row_to_pet(self, row: sqlite3.Row) -> Pet:
        row_dict = dict(row)
        row_dict["updated_at"] = datetime.fromisoformat(row_dict["updated_at"])
        return Pet(**row_dict)
    