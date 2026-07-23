import sqlite3
import pytest

from trowel_py.db.migrate import run_migrations
from trowel_py.cards.repository import create_card_repository
from trowel_py.cards.explanation_history_repository import (
    create_explanation_history_repository,
    ExplanationHistoryEntry,
)


def _seed_card(conn: sqlite3.Connection, card_id: str = "card-1") -> None:
    """insert a parent card so FK-referencing history rows can be created."""
    conn.execute(
        "insert into cards (id, title, category, explanation, tags) "
        "values (?, ?, ?, ?, ?)",
        (card_id, "test card", "python", "for FK tests", '["python"]'),
    )


def _entry(
    entry_id: str,
    card_id: str = "card-1",
    explanation: str = "an explanation",
    source: str = "llm",
) -> ExplanationHistoryEntry:
    """build a history entry with sensible defaults; override per test."""
    return ExplanationHistoryEntry(
        id=entry_id,
        card_id=card_id,
        explanation=explanation,
        source=source,
        created_at="",  # not used on insert; DB fills created_at via default
    )


def test_create_and_read_back(db_connection: sqlite3.Connection):
    """create a history row, then read it back via find_by_card_id."""
    run_migrations(db_connection)
    _seed_card(db_connection)
    repo = create_explanation_history_repository(db_connection)

    repo.create(_entry("h-1", explanation="first version"))

    rows = repo.find_by_card_id("card-1")
    assert len(rows) == 1
    assert rows[0].explanation == "first version"
    assert rows[0].source == "llm"
    # DB filled created_at via default — it must not be empty on read-back
    assert rows[0].created_at != ""


def test_find_by_card_id_orders_oldest_first(db_connection: sqlite3.Connection):
    """multiple versions come back ordered by created_at ascending (timeline)."""
    run_migrations(db_connection)
    _seed_card(db_connection)
    repo = create_explanation_history_repository(db_connection)

    # insert two versions with a real time gap so created_at differs
    repo.create(_entry("h-1", explanation="old"))
    db_connection.execute("UPDATE card_explanation_history SET created_at = '2026-01-01 10:00:00' WHERE id = 'h-1'")
    repo.create(_entry("h-2", explanation="new"))
    db_connection.execute("UPDATE card_explanation_history SET created_at = '2026-01-02 10:00:00' WHERE id = 'h-2'")

    rows = repo.find_by_card_id("card-1")
    assert [r.id for r in rows] == ["h-1", "h-2"], "oldest first"


def test_find_latest_returns_newest(db_connection: sqlite3.Connection):
    """find_latest returns the most recent version."""
    run_migrations(db_connection)
    _seed_card(db_connection)
    repo = create_explanation_history_repository(db_connection)

    repo.create(_entry("h-1", explanation="old"))
    db_connection.execute("UPDATE card_explanation_history SET created_at = '2026-01-01 10:00:00' WHERE id = 'h-1'")
    repo.create(_entry("h-2", explanation="new"))
    db_connection.execute("UPDATE card_explanation_history SET created_at = '2026-01-02 10:00:00' WHERE id = 'h-2'")

    latest = repo.find_latest("card-1")
    assert latest is not None
    assert latest.id == "h-2"


def test_find_returns_empty_for_nonexistent_card(db_connection: sqlite3.Connection):
    """a card with no history: find_by_card_id -> [], find_latest -> None."""
    run_migrations(db_connection)
    repo = create_explanation_history_repository(db_connection)

    assert repo.find_by_card_id("no-such-card") == []
    assert repo.find_latest("no-such-card") is None


def test_create_fk_violation_raises(db_connection: sqlite3.Connection):
    """inserting a history row for a non-existent card must raise (FK enforced)."""
    run_migrations(db_connection)
    repo = create_explanation_history_repository(db_connection)
    # note: no card seeded, so 'ghost-card' violates the FK
    with pytest.raises(sqlite3.IntegrityError):
        repo.create(_entry("h-1", card_id="ghost-card"))


def test_create_duplicate_id_raises(db_connection: sqlite3.Connection):
    """inserting a duplicate id must raise (primary key conflict)."""
    run_migrations(db_connection)
    _seed_card(db_connection)
    repo = create_explanation_history_repository(db_connection)

    repo.create(_entry("h-1"))
    with pytest.raises(sqlite3.IntegrityError):
        repo.create(_entry("h-1"))


def test_create_invalid_source_raises(db_connection: sqlite3.Connection):
    """source outside ('original','llm','user') must raise (CHECK constraint)."""
    run_migrations(db_connection)
    _seed_card(db_connection)
    repo = create_explanation_history_repository(db_connection)

    with pytest.raises(sqlite3.IntegrityError):
        repo.create(_entry("h-1", source="gpt"))
