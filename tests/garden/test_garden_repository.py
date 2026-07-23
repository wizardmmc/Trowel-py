import sqlite3
from trowel_py.db.migrate import run_migrations
from trowel_py.garden.repository import create_garden_repository


def _insert_card(
    conn: sqlite3.Connection, card_id: str, category: str = "python"
) -> None:
    conn.execute(
        "INSERT INTO cards (id, title, category, explanation, status, tags, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, 'active', '[]', datetime('now'), datetime('now'))",
        (
            card_id,
            f"Title of {card_id}",
            category,
            f"Explanation for {card_id} is long enough",
        ),
    )


def _insert_fsrs(conn: sqlite3.Connection, card_id: str, state: int, due: str) -> None:
    conn.execute(
        "INSERT INTO fsrs_state (card_id, state, stability, difficulty, reps, lapses, due) "
        "VALUES (?, ?, 1.0, 1.0, 3, 0, ?)",
        (card_id, state, due),
    )


def test_get_all_plants_with_fsrs(db_connection: sqlite3.Connection):
    run_migrations(db_connection)
    _insert_card(db_connection, "c1")
    _insert_fsrs(db_connection, "c1", state=2, due="2026-12-01T00:00:00+00:00")

    repo = create_garden_repository(db_connection)
    results = repo.get_all_plants()

    assert len(results) == 1
    row = results[0]
    assert row["id"] == "c1"
    assert row["state"] == 2
    assert row["reps"] == 3
    assert row["due"] is not None


def test_get_all_plants_empty(db_connection: sqlite3.Connection):
    run_migrations(db_connection)
    repo = create_garden_repository(db_connection)
    results = repo.get_all_plants()
    assert results == []


def test_get_all_plants_mixed_fsrs(db_connection: sqlite3.Connection):
    run_migrations(db_connection)
    _insert_card(db_connection, "with-fsrs")
    _insert_card(db_connection, "without-fsrs")
    _insert_fsrs(
        db_connection,
        "with-fsrs",
        state=2,
        due="2026-12-01T00:00:00+00:00",
    )

    repo = create_garden_repository(db_connection)
    results = repo.get_all_plants()

    assert len(results) == 2
    by_id = {r["id"]: r for r in results}
    assert by_id["with-fsrs"]["state"] == 2
    assert by_id["with-fsrs"]["due"] is not None
    assert by_id["without-fsrs"]["state"] is None
    assert by_id["without-fsrs"]["due"] is None
    assert by_id["without-fsrs"]["reps"] is None


def test_get_stats_mixed_states(db_connection: sqlite3.Connection):
    run_migrations(db_connection)
    past = "2020-01-01T00:00:00+00:00"
    future = "2099-01-01T00:00:00+00:00"

    _insert_card(db_connection, "tree-not-due")
    _insert_card(db_connection, "sprout-due")
    _insert_card(db_connection, "tree-due")
    _insert_fsrs(db_connection, "tree-not-due", state=2, due=future)
    _insert_fsrs(db_connection, "sprout-due", state=1, due=past)
    _insert_fsrs(db_connection, "tree-due", state=2, due=past)

    repo = create_garden_repository(db_connection)
    stats = repo.get_stats()

    assert stats["total_plants"] == 3
    assert stats["due_count"] == 2
    assert stats["flowering_rate"] == 66.7


def test_get_stats_empty(db_connection: sqlite3.Connection):
    run_migrations(db_connection)
    repo = create_garden_repository(db_connection)
    stats = repo.get_stats()

    assert stats["total_plants"] == 0
    assert stats["due_count"] == 0
    assert stats["flowering_rate"] == 0.0


def test_get_stats_no_fsrs(db_connection: sqlite3.Connection):
    run_migrations(db_connection)
    _insert_card(db_connection, "c1")

    repo = create_garden_repository(db_connection)
    stats = repo.get_stats()

    assert stats["total_plants"] == 1
    assert stats["due_count"] == 0
    assert stats["flowering_rate"] == 0.0
