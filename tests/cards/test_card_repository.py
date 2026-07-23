import sqlite3
from trowel_py.schemas.card import Card
from trowel_py.db.migrate import run_migrations
from trowel_py.cards.repository import create_card_repository


def _create_test_card(
    card_id: str, title: str = "Test Card", category: str = "test"
) -> Card:
    return Card(
        id=card_id,
        title=title,
        category=category,
        explanation="a test card explanation with enough length",
    )


def test_create_and_find_by_id(db_connection: sqlite3.Connection):
    run_migrations(db_connection)
    repo = create_card_repository(db_connection)
    card = _create_test_card("card-1")
    repo.create(card)
    found = repo.find_by_id("card-1")
    assert found is not None
    assert found.title == card.title
    assert found.category == card.category


def test_find_by_id_returns_none_when_missing(db_connection: sqlite3.Connection):
    run_migrations(db_connection)
    repo = create_card_repository(db_connection)
    assert repo.find_by_id("nonexistent") is None


def test_update_persists_card_changes(db_connection: sqlite3.Connection):
    run_migrations(db_connection)
    repo = create_card_repository(db_connection)
    card = _create_test_card("card-1", title="Old Title")
    repo.create(card)
    updated_card = _create_test_card("card-1", title="New Title", category="updated")
    repo.update("card-1", updated_card)
    found = repo.find_by_id("card-1")
    assert found is not None
    assert found.title == "New Title"
    assert found.category == "updated"


def test_find_all_returns_every_card(db_connection: sqlite3.Connection):
    run_migrations(db_connection)
    repo = create_card_repository(db_connection)
    repo.create(_create_test_card("card-1", title="First"))
    repo.create(_create_test_card("card-2", title="Second"))
    all_cards = repo.find_all()
    assert len(all_cards) == 2
    titles = {c.title for c in all_cards}
    assert titles == {"First", "Second"}


def test_search_by_fts5(db_connection: sqlite3.Connection):
    run_migrations(db_connection)
    repo = create_card_repository(db_connection)
    repo.create(
        Card(
            id="c1",
            title="Python Basics",
            category="python",
            explanation="Learn Python programming fundamentals",
        )
    )
    repo.create(
        Card(
            id="c2",
            title="Java Basics",
            category="java",
            explanation="Learn Java programming fundamentals",
        )
    )
    results = repo.search_by_fts5("python")
    assert len(results) == 1
    assert results[0].id == "c1"


def test_search_by_fts5_no_match(db_connection: sqlite3.Connection):
    run_migrations(db_connection)
    repo = create_card_repository(db_connection)
    repo.create(_create_test_card("card-1"))
    results = repo.search_by_fts5("nonexistent_keyword_xyz")
    assert results == []
