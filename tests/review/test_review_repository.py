import sqlite3
from datetime import datetime, timezone
from unittest.mock import patch

from trowel_py.cards.repository import create_card_repository
from trowel_py.db.migrate import run_migrations
from trowel_py.review.repository import create_review_repository
from trowel_py.schemas.card import Card
from trowel_py.schemas.review import FSRSState, ReviewLog


def _setup_repositories(conn: sqlite3.Connection):
    run_migrations(conn)
    return create_card_repository(conn), create_review_repository(conn)


def _insert_card(card_repo, card_id: str = "card-1") -> None:
    card_repo.create(
        Card(
            id=card_id,
            title="Test",
            category="test",
            explanation="a test card for review repository",
        )
    )


def _insert_fsrs_state(
    conn: sqlite3.Connection, card_id: str = "card-1", **overrides
) -> None:
    values = {
        "stability": 1.0,
        "difficulty": 5.0,
        "elapsed_days": 0,
        "scheduled_days": 1,
        "reps": 1,
        "lapses": 0,
        "state": 1,
        "due": "2025-01-01T00:00:00",
        "last_review": "2025-01-01T00:00:00",
    }
    values.update(overrides)
    conn.execute(
        "INSERT INTO fsrs_state (card_id, stability, difficulty, elapsed_days, "
        "scheduled_days, reps, lapses, state, due, last_review) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            card_id,
            values["stability"],
            values["difficulty"],
            values["elapsed_days"],
            values["scheduled_days"],
            values["reps"],
            values["lapses"],
            values["state"],
            values["due"],
            values["last_review"],
        ),
    )


class TestReviewLogs:
    def test_save_review_log_persists_rating_and_card(
        self, db_connection: sqlite3.Connection
    ):
        card_repo, review_repo = _setup_repositories(db_connection)
        _insert_card(card_repo)

        review_repo.save_review_log(
            ReviewLog(id="log-1", card_id="card-1", rating=3, state=0)
        )

        row = db_connection.execute(
            "SELECT * FROM review_logs WHERE id = ?", ("log-1",)
        ).fetchone()
        assert row is not None
        assert row["rating"] == 3
        assert row["card_id"] == "card-1"

    def test_save_review_log_persists_all_fields_and_returns_input(
        self, db_connection: sqlite3.Connection
    ):
        card_repo, review_repo = _setup_repositories(db_connection)
        _insert_card(card_repo)
        created_at = datetime(2026, 7, 23, 12, 30, tzinfo=timezone.utc)
        log = ReviewLog(
            id="log-1",
            card_id="card-1",
            rating=2,
            state=1,
            elapsed_days=4,
            scheduled_days=6,
            duration_ms=850,
            created_at=created_at,
        )

        result = review_repo.save_review_log(log)

        assert result is log
        row = db_connection.execute(
            "SELECT * FROM review_logs WHERE id = ?", ("log-1",)
        ).fetchone()
        assert dict(row) == {
            "id": "log-1",
            "card_id": "card-1",
            "rating": 2,
            "state": 1,
            "elapsed_days": 4,
            "scheduled_days": 6,
            "duration_ms": 850,
            "created_at": created_at.isoformat(),
        }

    def test_save_review_log_encodes_model_dump_datetime(
        self, db_connection: sqlite3.Connection
    ):
        card_repo, review_repo = _setup_repositories(db_connection)
        _insert_card(card_repo)
        log = ReviewLog(id="log-1", card_id="card-1", rating=3, state=0)
        dumped = log.model_dump()
        dumped_at = datetime(2030, 1, 2, 3, 4, tzinfo=timezone.utc)
        dumped["created_at"] = dumped_at

        with patch.object(ReviewLog, "model_dump", return_value=dumped):
            review_repo.save_review_log(log)

        row = db_connection.execute(
            "SELECT created_at FROM review_logs WHERE id = ?", ("log-1",)
        ).fetchone()
        assert row["created_at"] == dumped_at.isoformat()


class TestSaveFsrsState:
    def test_round_trips_all_fields_and_returns_input(
        self, db_connection: sqlite3.Connection
    ):
        card_repo, review_repo = _setup_repositories(db_connection)
        _insert_card(card_repo)
        due = datetime(2026, 8, 1, 8, 30, tzinfo=timezone.utc)
        last_review = datetime(2026, 7, 23, 12, 30, tzinfo=timezone.utc)
        state = FSRSState(
            card_id="card-1",
            stability=4.5,
            difficulty=6.25,
            elapsed_days=2,
            scheduled_days=9,
            reps=7,
            lapses=1,
            state=2,
            due=due,
            last_review=last_review,
        )

        result = review_repo.save_fsrs_state(state)

        assert result is state
        assert review_repo.find_by_card_id("card-1") == state

    def test_encodes_model_dump_datetime_values(
        self, db_connection: sqlite3.Connection
    ):
        card_repo, review_repo = _setup_repositories(db_connection)
        _insert_card(card_repo)
        state = FSRSState(card_id="card-1")
        dumped = state.model_dump()
        dumped_due = datetime(2030, 1, 3, 4, 5, tzinfo=timezone.utc)
        dumped_last_review = datetime(2030, 1, 2, 3, 4, tzinfo=timezone.utc)
        dumped["due"] = dumped_due
        dumped["last_review"] = dumped_last_review

        with patch.object(FSRSState, "model_dump", return_value=dumped):
            review_repo.save_fsrs_state(state)

        row = db_connection.execute(
            "SELECT due, last_review FROM fsrs_state WHERE card_id = ?",
            ("card-1",),
        ).fetchone()
        assert row["due"] == dumped_due.isoformat()
        assert row["last_review"] == dumped_last_review.isoformat()

    def test_round_trips_missing_last_review(self, db_connection: sqlite3.Connection):
        card_repo, review_repo = _setup_repositories(db_connection)
        _insert_card(card_repo)
        state = FSRSState(card_id="card-1", last_review=None)

        review_repo.save_fsrs_state(state)

        assert review_repo.find_by_card_id("card-1") == state


class TestFindDue:
    def test_returns_card_due_before_cutoff(self, db_connection: sqlite3.Connection):
        card_repo, review_repo = _setup_repositories(db_connection)
        _insert_card(card_repo)
        _insert_fsrs_state(db_connection, due="2020-01-01T00:00:00")

        results = review_repo.find_due("2025-01-01T00:00:00")

        assert len(results) == 1
        assert results[0].card_id == "card-1"

    def test_returns_empty_when_no_card_is_due(self, db_connection: sqlite3.Connection):
        _, review_repo = _setup_repositories(db_connection)

        assert review_repo.find_due("2025-01-01T00:00:00") == []


class TestFindFsrsState:
    def test_returns_state_for_existing_card(self, db_connection: sqlite3.Connection):
        card_repo, review_repo = _setup_repositories(db_connection)
        _insert_card(card_repo)
        _insert_fsrs_state(db_connection)

        result = review_repo.find_by_card_id("card-1")

        assert result is not None
        assert result.card_id == "card-1"
        assert result.stability == 1.0
        assert result.reps == 1

    def test_returns_none_when_card_has_no_state(
        self, db_connection: sqlite3.Connection
    ):
        _, review_repo = _setup_repositories(db_connection)

        assert review_repo.find_by_card_id("missing-card") is None


class TestUpdateFsrsState:
    def test_persists_changed_fields(self, db_connection: sqlite3.Connection):
        card_repo, review_repo = _setup_repositories(db_connection)
        _insert_card(card_repo)
        _insert_fsrs_state(db_connection)
        original = review_repo.find_by_card_id("card-1")
        assert original is not None
        updated = original.model_copy(
            update={
                "reps": 2,
                "stability": 2.5,
                "difficulty": 3.0,
                "state": 2,
            }
        )

        review_repo.update_fsrs_state(updated)

        row = db_connection.execute(
            "SELECT * FROM fsrs_state WHERE card_id = ?", ("card-1",)
        ).fetchone()
        assert row["reps"] == 2
        assert row["stability"] == 2.5
        assert row["state"] == 2

    def test_missing_card_is_a_noop(self, db_connection: sqlite3.Connection):
        _, review_repo = _setup_repositories(db_connection)
        state = FSRSState(card_id="missing-card", reps=1, state=1)

        result = review_repo.update_fsrs_state(state)

        assert result is state
        assert review_repo.find_by_card_id("missing-card") is None


class TestSessionStats:
    def test_empty_history_returns_zeros(self, db_connection: sqlite3.Connection):
        _, review_repo = _setup_repositories(db_connection)

        stats = review_repo.get_session_stats("2000-01-01T00:00:00")

        assert stats == {"total": 0, "avg_rating": 0.0, "accuracy": 0.0}

    def test_mixed_ratings_calculate_average_and_accuracy(
        self, db_connection: sqlite3.Connection
    ):
        card_repo, review_repo = _setup_repositories(db_connection)
        _insert_card(card_repo)
        for index, rating in enumerate([1, 3, 4]):
            review_repo.save_review_log(
                ReviewLog(
                    id=f"log-{index}",
                    card_id="card-1",
                    rating=rating,
                    state=0,
                )
            )

        stats = review_repo.get_session_stats("2000-01-01T00:00:00")

        assert stats == {"total": 3, "avg_rating": 2.67, "accuracy": 66.7}

    def test_all_bad_ratings_have_zero_accuracy(
        self, db_connection: sqlite3.Connection
    ):
        card_repo, review_repo = _setup_repositories(db_connection)
        _insert_card(card_repo)
        for index in range(3):
            review_repo.save_review_log(
                ReviewLog(
                    id=f"log-{index}",
                    card_id="card-1",
                    rating=1,
                    state=0,
                )
            )

        stats = review_repo.get_session_stats("2000-01-01T00:00:00")

        assert stats["accuracy"] == 0.0

    def test_excludes_logs_before_start_time(self, db_connection: sqlite3.Connection):
        card_repo, review_repo = _setup_repositories(db_connection)
        _insert_card(card_repo)
        db_connection.execute(
            "INSERT INTO review_logs (id, card_id, rating, state, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            ("old-log", "card-1", 3, 0, "2020-01-01T00:00:00"),
        )
        review_repo.save_review_log(
            ReviewLog(id="new-log", card_id="card-1", rating=3, state=0)
        )

        stats = review_repo.get_session_stats("2025-01-01T00:00:00")

        assert stats["total"] == 1
