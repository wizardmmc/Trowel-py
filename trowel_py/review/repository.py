import sqlite3
from datetime import datetime
from trowel_py.schemas.review import FSRSState, ReviewLog


def create_review_repository(conn: sqlite3.Connection):
    return ReviewRepository(conn)


class ReviewRepository:
    """
    intermediate layer that hides SQL details behind review queries
    """
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def find_due(self, before: str) -> list[FSRSState]:
        """
        find cards that are due for review before the given time
        """
        rows = self.conn.execute(
            "select * from fsrs_state where due <= ?",
            (before,)
        ).fetchall()
        res = []
        for row in rows:
            res.append(self._row_to_fsrs_state(row))
        return res

    def save_review_log(self, log: ReviewLog) -> ReviewLog:
        """
        record a review log entry
        """
        data = log.model_dump()
        data["created_at"] = data["created_at"].isoformat()
        self.conn.execute(
            "insert into review_logs (id, card_id, rating, state, elapsed_days, scheduled_days, duration_ms, created_at) values (?, ?, ?, ?, ?, ?, ?, ?)",
            (data["id"], data["card_id"], data["rating"], data["state"],
             data["elapsed_days"], data["scheduled_days"], data["duration_ms"],
             data["created_at"])
        )
        return log

    def _row_to_fsrs_state(self, row: sqlite3.Row) -> FSRSState:
        """
        convert sqlite3.Row to FSRSState, parse datetime strings back
        """
        row_dict = dict(row)
        row_dict["due"] = datetime.fromisoformat(row_dict["due"]) if row_dict["due"] else None
        row_dict["last_review"] = datetime.fromisoformat(row_dict["last_review"]) if row_dict["last_review"] else None
        return FSRSState(**row_dict)

    def save_fsrs_state(self, fsrs_state: FSRSState) -> FSRSState:
        """
        record a card's fsrs state
        """
        # convert into dict
        data = fsrs_state.model_dump()
        data["due"] = data["due"].isoformat()
        data["last_review"] = data["last_review"].isoformat() if data["last_review"] else None
        
        self.conn.execute(
            "insert into fsrs_state (card_id, stability, difficulty, elapsed_days, scheduled_days, reps, lapses, state, due, last_review) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", 
            (data["card_id"], data["stability"], data["difficulty"], data["elapsed_days"], data["scheduled_days"], data["reps"], data["lapses"], data["state"], data["due"], data["last_review"])
        )
        return fsrs_state

