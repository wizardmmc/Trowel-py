import sqlite3
from datetime import datetime
from trowel_py.schemas.card import Card
from trowel_py.schemas.review import FSRSState, ReviewLog
from trowel_py.db.migrate import run_migrations
from trowel_py.cards.repository import create_card_repository
from trowel_py.review.repository import create_review_repository


def _insert_test_card(repo, card_id: str = "card-1") -> None:
    """helper: insert a card so foreign keys work"""
    repo.create(Card(id=card_id, title="Test", category="test", explanation="a test card for review repo"))


def test_save_review_log(db_connection: sqlite3.Connection):
    """save a review log, then verify it exists in the database"""
    run_migrations(db_connection)
    card_repo = create_card_repository(db_connection)
    review_repo = create_review_repository(db_connection)
    _insert_test_card(card_repo)
    log = ReviewLog(id="log-1", card_id="card-1", rating=3, state=0)
    review_repo.save_review_log(log)
    # verify by querying directly
    row = db_connection.execute("select * from review_logs where id = ?", ("log-1",)).fetchone()
    assert row is not None
    assert row["rating"] == 3
    assert row["card_id"] == "card-1"


def test_find_due(db_connection: sqlite3.Connection):
    """insert fsrs_state with past due, find_due returns it"""
    run_migrations(db_connection)
    card_repo = create_card_repository(db_connection)
    review_repo = create_review_repository(db_connection)
    _insert_test_card(card_repo)
    # insert a fsrs_state record with due in the past
    db_connection.execute(
        "insert into fsrs_state (card_id, state, due) values (?, ?, ?)",
        ("card-1", 0, "2020-01-01 00:00:00")
    )
    results = review_repo.find_due("2025-01-01")
    assert len(results) == 1
    assert results[0].card_id == "card-1"


def test_find_due_empty(db_connection: sqlite3.Connection):
    """no due cards returns empty list"""
    run_migrations(db_connection)
    review_repo = create_review_repository(db_connection)
    results = review_repo.find_due("2025-01-01")
    assert results == []
