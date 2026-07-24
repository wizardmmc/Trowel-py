import sqlite3
from datetime import datetime

from trowel_py.schemas.review import FSRSState, ReviewLog


def _review_log_record(log: ReviewLog) -> dict[str, object]:
    data = log.model_dump()
    data["created_at"] = data["created_at"].isoformat()
    return data


def _fsrs_record(state: FSRSState) -> dict[str, object]:
    data = state.model_dump()
    data["due"] = data["due"].isoformat()
    data["last_review"] = (
        data["last_review"].isoformat() if data["last_review"] else None
    )
    return data


def create_review_repository(conn: sqlite3.Connection):
    return ReviewRepository(conn)


class ReviewRepository:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def find_due(self, before: str) -> list[FSRSState]:
        rows = self.conn.execute(
            "select * from fsrs_state where due <= ?", (before,)
        ).fetchall()
        return [self._row_to_fsrs_state(row) for row in rows]

    def save_review_log(self, log: ReviewLog) -> ReviewLog:
        data = _review_log_record(log)
        self.conn.execute(
            "insert into review_logs "
            "(id, card_id, rating, state, elapsed_days, scheduled_days, "
            "duration_ms, created_at) values (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                data["id"],
                data["card_id"],
                data["rating"],
                data["state"],
                data["elapsed_days"],
                data["scheduled_days"],
                data["duration_ms"],
                data["created_at"],
            ),
        )
        return log

    def _row_to_fsrs_state(self, row: sqlite3.Row) -> FSRSState:
        data = dict(row)
        data["due"] = datetime.fromisoformat(data["due"]) if data["due"] else None
        data["last_review"] = (
            datetime.fromisoformat(data["last_review"]) if data["last_review"] else None
        )
        return FSRSState(**data)

    def save_fsrs_state(self, fsrs_state: FSRSState) -> FSRSState:
        data = _fsrs_record(fsrs_state)
        self.conn.execute(
            "insert into fsrs_state "
            "(card_id, stability, difficulty, elapsed_days, scheduled_days, "
            "reps, lapses, state, due, last_review) "
            "values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                data["card_id"],
                data["stability"],
                data["difficulty"],
                data["elapsed_days"],
                data["scheduled_days"],
                data["reps"],
                data["lapses"],
                data["state"],
                data["due"],
                data["last_review"],
            ),
        )
        return fsrs_state

    def update_fsrs_state(self, fsrs_state: FSRSState) -> FSRSState:
        data = _fsrs_record(fsrs_state)
        self.conn.execute(
            "update fsrs_state set stability=?, difficulty=?, elapsed_days=?, "
            "scheduled_days=?, reps=?, lapses=?, state=?, due=?, last_review=? "
            "where card_id=?",
            (
                data["stability"],
                data["difficulty"],
                data["elapsed_days"],
                data["scheduled_days"],
                data["reps"],
                data["lapses"],
                data["state"],
                data["due"],
                data["last_review"],
                data["card_id"],
            ),
        )
        return fsrs_state

    def find_by_card_id(self, card_id: str) -> FSRSState | None:
        row = self.conn.execute(
            "select * from fsrs_state where card_id = ?", (card_id,)
        ).fetchone()
        if row is None:
            return None
        return self._row_to_fsrs_state(row)

    def find_all_states(self) -> list[FSRSState]:
        rows = self.conn.execute("select * from fsrs_state").fetchall()
        return [self._row_to_fsrs_state(row) for row in rows]

    def get_session_stats(self, since: str) -> dict:
        row = self.conn.execute(
            "select count(*) as total, avg(rating) as avg_rating, "
            "sum(case when rating >= 3 then 1 else 0 end) as correct "
            "from review_logs where created_at >= ?",
            (since,),
        ).fetchone()
        if row is None or row["total"] == 0:
            return {"total": 0, "avg_rating": 0.0, "accuracy": 0.0}
        total = row["total"]
        accuracy = round((row["correct"] / total) * 100, 1)
        return {
            "total": total,
            "avg_rating": round(row["avg_rating"], 2),
            "accuracy": accuracy,
        }
