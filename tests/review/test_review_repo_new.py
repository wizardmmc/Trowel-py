"""Tests for review repository — new methods added in Slice 006."""
import sqlite3

from trowel_py.schemas.card import Card
from trowel_py.schemas.review import FSRSState, ReviewLog
from trowel_py.db.migrate import run_migrations
from trowel_py.cards.repository import create_card_repository
from trowel_py.review.repository import create_review_repository


def _setup_repos(conn: sqlite3.Connection):
    """Helper: run migrations, return (card_repo, review_repo)."""
    run_migrations(conn)
    return create_card_repository(conn), create_review_repository(conn)


def _insert_card(card_repo, card_id: str = "card-1") -> None:
    """Helper: insert a card so foreign keys work."""
    card_repo.create(Card(id=card_id, title="Test", category="test", explanation="a test card for review repo"))


def _insert_fsrs_state(conn: sqlite3.Connection, card_id: str = "card-1", **overrides) -> None:
    """Helper: directly insert an fsrs_state row."""
    defaults = {
        "stability": 1.0, "difficulty": 5.0, "elapsed_days": 0,
        "scheduled_days": 1, "reps": 1, "lapses": 0, "state": 1,
        "due": "2025-01-01T00:00:00", "last_review": "2025-01-01T00:00:00",
    }
    defaults.update(overrides)
    conn.execute(
        "INSERT INTO fsrs_state (card_id, stability, difficulty, elapsed_days, "
        "scheduled_days, reps, lapses, state, due, last_review) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (card_id, defaults["stability"], defaults["difficulty"],
         defaults["elapsed_days"], defaults["scheduled_days"],
         defaults["reps"], defaults["lapses"], defaults["state"],
         defaults["due"], defaults["last_review"]),
    )


class TestFindByCardId:
    """Tests for find_by_card_id()."""

    def test_found_returns_state(self, db_connection: sqlite3.Connection):
        """Finding existing card_id should return FSRSState."""
        card_repo, review_repo = _setup_repos(db_connection)
        _insert_card(card_repo, "card-1")
        _insert_fsrs_state(db_connection, "card-1")

        result = review_repo.find_by_card_id("card-1")

        assert result is not None
        assert result.card_id == "card-1"
        assert result.stability == 1.0
        assert result.reps == 1

    def test_not_found_returns_none(self, db_connection: sqlite3.Connection):
        """Non-existent card_id should return None."""
        card_repo, review_repo = _setup_repos(db_connection)
        result = review_repo.find_by_card_id("nonexistent")
        assert result is None


class TestUpdateFsrsState:
    """Tests for update_fsrs_state()."""

    def test_update_changes_fields(self, db_connection: sqlite3.Connection):
        """Updating state should persist new values in DB."""
        card_repo, review_repo = _setup_repos(db_connection)
        _insert_card(card_repo, "card-1")
        _insert_fsrs_state(db_connection, "card-1", reps=1, stability=1.0)

        # Load, modify, update
        original = review_repo.find_by_card_id("card-1")
        updated = original.model_copy(update={
            "reps": 2, "stability": 2.5, "difficulty": 3.0, "state": 2,
        })
        review_repo.update_fsrs_state(updated)

        # Verify in DB
        row = db_connection.execute("SELECT * FROM fsrs_state WHERE card_id = ?", ("card-1",)).fetchone()
        assert row["reps"] == 2
        assert row["stability"] == 2.5
        assert row["state"] == 2

    def test_update_nonexistent_does_not_crash(self, db_connection: sqlite3.Connection):
        """Updating a card_id that doesn't exist should not raise."""
        _, review_repo = _setup_repos(db_connection)
        state = FSRSState(card_id="ghost", reps=1, state=1)
        # Should not raise, just affects 0 rows
        review_repo.update_fsrs_state(state)


class TestGetSessionStats:
    """Tests for get_session_stats() — SQL aggregation."""

    def test_no_logs_returns_zeros(self, db_connection: sqlite3.Connection):
        """No review logs should return zeroed stats."""
        _, review_repo = _setup_repos(db_connection)
        stats = review_repo.get_session_stats("2000-01-01T00:00:00")

        assert stats["total"] == 0
        assert stats["avg_rating"] == 0.0
        assert stats["accuracy"] == 0.0

    def test_mixed_ratings_correct_accuracy(self, db_connection: sqlite3.Connection):
        """3 logs with ratings 1,3,4 → accuracy = 66.7% (2 of 3 are Good+)."""
        card_repo, review_repo = _setup_repos(db_connection)
        _insert_card(card_repo, "card-1")

        for i, rating in enumerate([1, 3, 4]):
            log = ReviewLog(id=f"log-{i}", card_id="card-1", rating=rating, state=0)
            review_repo.save_review_log(log)

        stats = review_repo.get_session_stats("2000-01-01T00:00:00")

        assert stats["total"] == 3
        assert stats["avg_rating"] == 2.67  # (1+3+4)/3
        assert stats["accuracy"] == 66.7  # 2/3 * 100, rounded to 1 decimal

    def test_all_bad_ratings_zero_accuracy(self, db_connection: sqlite3.Connection):
        """All ratings < 3 should produce 0% accuracy."""
        card_repo, review_repo = _setup_repos(db_connection)
        _insert_card(card_repo, "card-1")

        for i in range(3):
            log = ReviewLog(id=f"log-{i}", card_id="card-1", rating=1, state=0)
            review_repo.save_review_log(log)

        stats = review_repo.get_session_stats("2000-01-01T00:00:00")
        assert stats["accuracy"] == 0.0

    def test_since_filters_correctly(self, db_connection: sqlite3.Connection):
        """Logs before the 'since' timestamp should be excluded."""
        card_repo, review_repo = _setup_repos(db_connection)
        _insert_card(card_repo, "card-1")

        # Insert one old log and one recent log
        db_connection.execute(
            "INSERT INTO review_logs (id, card_id, rating, state, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            ("old-log", "card-1", 3, 0, "2020-01-01T00:00:00"),
        )
        log = ReviewLog(id="new-log", card_id="card-1", rating=3, state=0)
        review_repo.save_review_log(log)

        # Only count logs after 2025
        stats = review_repo.get_session_stats("2025-01-01T00:00:00")
        assert stats["total"] == 1  # only the new one
