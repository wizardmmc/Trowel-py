import sqlite3
from unittest.mock import MagicMock, patch

from trowel_py.db.migrate import run_migrations
from trowel_py.garden.repository import GardenRepository, create_garden_repository
from trowel_py.garden.service import get_plants, get_stats


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
        "VALUES (?, ?, 1.0, 1.0, 5, 0, ?)",
        (card_id, state, due),
    )


def test_get_plants_returns_plant_stage(db_connection: sqlite3.Connection):
    run_migrations(db_connection)
    _insert_card(db_connection, "c1")
    _insert_fsrs(db_connection, "c1", state=2, due="2099-01-01T00:00:00+00:00")

    repo = create_garden_repository(db_connection)
    plants = get_plants(repo)

    assert len(plants) == 1
    assert plants[0]["plant_stage"] == "tree"
    assert plants[0]["card_id"] == "c1"
    assert plants[0]["fsrs_state"] == 2


def test_get_plants_no_fsrs_is_seed(db_connection: sqlite3.Connection):
    run_migrations(db_connection)
    _insert_card(db_connection, "c1")

    repo = create_garden_repository(db_connection)
    plants = get_plants(repo)

    assert len(plants) == 1
    assert plants[0]["plant_stage"] == "seed"
    assert plants[0]["fsrs_state"] is None
    assert plants[0]["reps"] == 0


def test_get_plants_returns_empty_when_no_cards(db_connection: sqlite3.Connection):
    run_migrations(db_connection)
    repo = create_garden_repository(db_connection)
    plants = get_plants(repo)
    assert plants == []


def test_get_plants_maps_repository_row_without_mutating_it():
    row = {
        "id": "c1",
        "title": "Python Decorators",
        "category": "python",
        "explanation": "Decorators wrap functions",
        "state": 3,
        "stability": 2.5,
        "reps": None,
        "due": "2099-01-01T00:00:00+00:00",
    }
    original_row = row.copy()
    repo = MagicMock(spec=GardenRepository)
    repo.get_all_plants.return_value = [row]

    with patch(
        "trowel_py.garden.service.get_plant_stage",
        return_value="wilting",
    ) as get_stage:
        plants = get_plants(repo)

    assert plants == [
        {
            "card_id": "c1",
            "title": "Python Decorators",
            "category": "python",
            "explanation": "Decorators wrap functions",
            "plant_stage": "wilting",
            "fsrs_state": 3,
            "due": "2099-01-01T00:00:00+00:00",
            "reps": 0,
        }
    ]
    assert row == original_row
    get_stage.assert_called_once_with(3)


def test_get_stats_passes_through(db_connection: sqlite3.Connection):
    run_migrations(db_connection)
    _insert_card(db_connection, "c1")
    _insert_fsrs(db_connection, "c1", state=2, due="2020-01-01T00:00:00+00:00")

    repo = create_garden_repository(db_connection)
    stats = get_stats(repo)

    assert stats["total_plants"] == 1
    assert stats["due_count"] == 1
    assert stats["flowering_rate"] == 100.0
