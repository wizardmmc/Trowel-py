import sqlite3
from datetime import datetime, timezone

def create_garden_repository(conn: sqlite3.Connection):
    return GardenRepository(conn)


class GardenRepository:
    """
    intermediate layer that hides SQL details behind review queries
    """
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def get_all_plants(self) -> list[dict]:
        """
        left join cards & fsrs_state, return all plants's data
        """
        rows = self.conn.execute(
            "select c.id, c.title, c.category, c.explanation, "
            "s.state, s.stability, s.reps, s.due "
            "from cards c left join fsrs_state s on c.id = s.card_id"
        ).fetchall()
        return [dict(row) for row in rows]
    
    def get_stats(self) -> dict:
        """
        return some statistic data
        """
        now = datetime.now(timezone.utc).isoformat()
        row = self.conn.execute(
            "select count(*) as total_plants, "
            "sum(case when s.due <= ? then 1 else 0 end) as due_count, "
            "sum(case when s.state = 2 then 1 else 0 end) as flowering_count "
            "from cards c left join fsrs_state s on c.id = s.card_id", (now, )
        ).fetchone()
        total = row["total_plants"] or 0    # avoid null
        due_count = row["due_count"] or 0
        flowering_count = row["flowering_count"] or 0
        flowering_rate = round((flowering_count / total) * 100, 1) if total > 0 else 0.0
        return {
            "total_plants": total,
            "due_count": due_count,
            "flowering_rate": flowering_rate,
        }
