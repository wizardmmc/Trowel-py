"""Tests for review service — business logic unit tests."""
import sqlite3
from datetime import datetime, timezone

from trowel_py.schemas.card import Card
from trowel_py.db.migrate import run_migrations
from trowel_py.cards.repository import create_card_repository
from trowel_py.review.repository import create_review_repository
from trowel_py.review.service import get_due_cards, submit_review, get_session_stats, get_review_stats


def _setup_repos(conn: sqlite3.Connection):
    """Helper: run migrations, return (card_repo, review_repo)."""
    run_migrations(conn)
    return create_card_repository(conn), create_review_repository(conn)


def _insert_card(card_repo, card_id: str = "card-1") -> None:
    """Helper: insert a card."""
    card_repo.create(Card(id=card_id, title="Test", category="test", explanation="a test card for review service"))


def _insert_fsrs_state(conn: sqlite3.Connection, card_id: str = "card-1", **overrides) -> None:
    """Helper: directly insert fsrs_state row."""
    from datetime import datetime
    defaults = {
        "stability": 0, "difficulty": 0, "elapsed_days": 0,
        "scheduled_days": 0, "reps": 0, "lapses": 0, "state": 0,
        "due": "2020-01-01T00:00:00", "last_review": None,
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


class TestGetDueCards:
    """Tests for get_due_cards()."""

    def test_returns_due_cards_with_card_data(self):
        """Due cards should include both FSRS state and card content."""
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        card_repo, review_repo = _setup_repos(conn)

        _insert_card(card_repo, "card-1")
        _insert_fsrs_state(conn, "card-1", due="2020-01-01T00:00:00")

        results = get_due_cards(review_repo, card_repo)

        assert len(results) == 1
        assert results[0]["card"].title == "Test"
        assert results[0]["fsrs_state"].card_id == "card-1"
        assert results[0]["plant_stage"] == "seed"  # state=0
        conn.close()

    def test_no_due_cards_returns_empty(self):
        """No due cards should return empty list."""
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        card_repo, review_repo = _setup_repos(conn)

        results = get_due_cards(review_repo, card_repo)
        assert results == []
        conn.close()

    def test_missing_card_skipped(self):
        """FSRS state pointing to deleted card should be skipped."""
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        # Start with FK OFF so we can insert orphan fsrs_state
        card_repo, review_repo = _setup_repos(conn)

        conn.execute("PRAGMA foreign_keys=OFF")
        _insert_fsrs_state(conn, "ghost-card", due="2020-01-01T00:00:00")
        conn.execute("PRAGMA foreign_keys=ON")

        results = get_due_cards(review_repo, card_repo)
        assert results == []  # skipped without crashing
        conn.close()


class TestSubmitReview:
    """Tests for submit_review()."""

    def test_normal_flow_returns_result(self):
        """Submitting a valid review should return updated state + log."""
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        card_repo, review_repo = _setup_repos(conn)

        _insert_card(card_repo, "card-1")
        _insert_fsrs_state(conn, "card-1", reps=0, state=0, due="2020-01-01T00:00:00")

        result = submit_review("card-1", 3, review_repo, card_repo)

        assert result is not None
        assert result["fsrs_state"].reps == 1
        assert result["review_log"].rating == 3
        assert result["plant_stage"] in ("seed", "sprout", "tree", "wilting")
        conn.close()

    def test_unknown_card_returns_none(self):
        """Submitting for non-existent card should return None."""
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        card_repo, review_repo = _setup_repos(conn)

        result = submit_review("nonexistent", 3, review_repo, card_repo)
        assert result is None
        conn.close()

    def test_no_fsrs_state_returns_none(self):
        """Card without FSRS state should return None."""
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        card_repo, review_repo = _setup_repos(conn)

        _insert_card(card_repo, "card-1")
        # No fsrs_state inserted

        result = submit_review("card-1", 3, review_repo, card_repo)
        assert result is None
        conn.close()

    def test_plant_changed_detected(self):
        """plant_changed should be True when plant stage transitions."""
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        card_repo, review_repo = _setup_repos(conn)

        _insert_card(card_repo, "card-1")
        _insert_fsrs_state(conn, "card-1", reps=0, state=0, due="2020-01-01T00:00:00")

        result = submit_review("card-1", 3, review_repo, card_repo)

        # New card (state 0=seed) → after review should be state 1=sprout
        assert result["plant_changed"] is True
        conn.close()


class TestGetStats:
    """Tests for get_session_stats() and get_review_stats()."""

    def test_session_stats_empty(self):
        """No reviews should return zeroed stats."""
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        _, review_repo = _setup_repos(conn)

        stats = get_session_stats(review_repo, "2000-01-01T00:00:00")
        assert stats["total"] == 0
        conn.close()

    def test_review_stats_uses_early_date(self):
        """get_review_stats should return all-time stats."""
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        card_repo, review_repo = _setup_repos(conn)
        _insert_card(card_repo, "card-1")

        log = __import__("trowel_py.schemas.review", fromlist=["ReviewLog"]).ReviewLog(
            id="log-1", card_id="card-1", rating=4, state=0,
        )
        review_repo.save_review_log(log)

        stats = get_review_stats(review_repo)
        assert stats["total"] == 1
        assert stats["accuracy"] == 100.0
        conn.close()
