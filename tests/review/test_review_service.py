import sqlite3

from trowel_py.schemas.card import Card
from trowel_py.schemas.review import ReviewLog
from trowel_py.db.migrate import run_migrations
from trowel_py.cards.repository import create_card_repository
from trowel_py.review.repository import create_review_repository
from trowel_py.review.service import (
    get_due_cards,
    submit_review,
    get_session_stats,
    get_review_stats,
)


def _setup_repos(conn: sqlite3.Connection):
    run_migrations(conn)
    return create_card_repository(conn), create_review_repository(conn)


def _insert_card(card_repo, card_id: str = "card-1") -> None:
    card_repo.create(
        Card(
            id=card_id,
            title="Test",
            category="test",
            explanation="a test card for review service",
        )
    )


def _insert_fsrs_state(
    conn: sqlite3.Connection, card_id: str = "card-1", **overrides
) -> None:
    defaults = {
        "stability": 0,
        "difficulty": 0,
        "elapsed_days": 0,
        "scheduled_days": 0,
        "reps": 0,
        "lapses": 0,
        "state": 0,
        "due": "2020-01-01T00:00:00",
        "last_review": None,
    }
    defaults.update(overrides)
    conn.execute(
        "INSERT INTO fsrs_state (card_id, stability, difficulty, elapsed_days, "
        "scheduled_days, reps, lapses, state, due, last_review) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            card_id,
            defaults["stability"],
            defaults["difficulty"],
            defaults["elapsed_days"],
            defaults["scheduled_days"],
            defaults["reps"],
            defaults["lapses"],
            defaults["state"],
            defaults["due"],
            defaults["last_review"],
        ),
    )


class TestGetDueCards:
    def test_returns_due_cards_with_card_data(
        self,
        db_connection: sqlite3.Connection,
    ):
        card_repo, review_repo = _setup_repos(db_connection)

        _insert_card(card_repo, "card-1")
        _insert_fsrs_state(
            db_connection,
            "card-1",
            due="2020-01-01T00:00:00",
        )

        results = get_due_cards(review_repo, card_repo)

        assert len(results) == 1
        assert results[0]["card"].title == "Test"
        assert results[0]["fsrs_state"].card_id == "card-1"
        assert results[0]["plant_stage"] == "seed"

    def test_no_due_cards_returns_empty(
        self,
        db_connection: sqlite3.Connection,
    ):
        card_repo, review_repo = _setup_repos(db_connection)

        results = get_due_cards(review_repo, card_repo)
        assert results == []

    def test_orphan_fsrs_state_is_skipped(
        self,
        db_connection: sqlite3.Connection,
    ):
        card_repo, review_repo = _setup_repos(db_connection)

        # 先关闭外键才能构造生产数据库不允许出现的孤儿状态，覆盖防御性分支。
        db_connection.commit()
        db_connection.execute("PRAGMA foreign_keys=OFF")
        _insert_fsrs_state(
            db_connection,
            "ghost-card",
            due="2020-01-01T00:00:00",
        )
        db_connection.commit()
        db_connection.execute("PRAGMA foreign_keys=ON")

        results = get_due_cards(review_repo, card_repo)
        assert results == []


class TestSubmitReview:
    def test_submit_review_updates_state_and_returns_log(
        self,
        db_connection: sqlite3.Connection,
    ):
        card_repo, review_repo = _setup_repos(db_connection)

        _insert_card(card_repo, "card-1")
        _insert_fsrs_state(
            db_connection,
            "card-1",
            reps=0,
            state=0,
            due="2020-01-01T00:00:00",
        )

        result = submit_review("card-1", 3, review_repo, card_repo)

        assert result is not None
        assert result["fsrs_state"].reps == 1
        assert result["review_log"].rating == 3
        assert result["plant_stage"] in ("seed", "sprout", "tree", "wilting")

    def test_unknown_card_returns_none(
        self,
        db_connection: sqlite3.Connection,
    ):
        card_repo, review_repo = _setup_repos(db_connection)

        result = submit_review("nonexistent", 3, review_repo, card_repo)
        assert result is None

    def test_no_fsrs_state_returns_none(
        self,
        db_connection: sqlite3.Connection,
    ):
        card_repo, review_repo = _setup_repos(db_connection)

        _insert_card(card_repo, "card-1")

        result = submit_review("card-1", 3, review_repo, card_repo)
        assert result is None

    def test_plant_changed_detected(
        self,
        db_connection: sqlite3.Connection,
    ):
        card_repo, review_repo = _setup_repos(db_connection)

        _insert_card(card_repo, "card-1")
        _insert_fsrs_state(
            db_connection,
            "card-1",
            reps=0,
            state=0,
            due="2020-01-01T00:00:00",
        )

        result = submit_review("card-1", 3, review_repo, card_repo)

        assert result["plant_changed"] is True


class TestGetStats:
    def test_session_stats_empty(
        self,
        db_connection: sqlite3.Connection,
    ):
        _, review_repo = _setup_repos(db_connection)

        stats = get_session_stats(review_repo, "2000-01-01T00:00:00")
        assert stats["total"] == 0

    def test_review_stats_include_all_history(
        self,
        db_connection: sqlite3.Connection,
    ):
        card_repo, review_repo = _setup_repos(db_connection)
        _insert_card(card_repo, "card-1")

        log = ReviewLog(
            id="log-1",
            card_id="card-1",
            rating=4,
            state=0,
        )
        review_repo.save_review_log(log)

        stats = get_review_stats(review_repo)
        assert stats["total"] == 1
        assert stats["accuracy"] == 100.0
