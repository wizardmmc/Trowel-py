import sqlite3

import pytest
from fastapi.testclient import TestClient

from trowel_py.app import create_app
from trowel_py.db.migrate import run_migrations
from trowel_py.garden.routes import _get_conn


def _seed_data(conn: sqlite3.Connection) -> None:
    conn.execute(
        "INSERT INTO cards (id, title, category, explanation, status, tags, created_at, updated_at) "
        "VALUES ('c1', 'Python Decorators', 'python', 'Decorators wrap functions', 'active', '[]', "
        "datetime('now'), datetime('now'))"
    )
    conn.execute(
        "INSERT INTO fsrs_state (card_id, state, stability, difficulty, reps, lapses, due) "
        "VALUES ('c1', 2, 5.0, 3.0, 10, 1, '2020-01-01T00:00:00')"
    )
    conn.execute(
        "INSERT INTO cards (id, title, category, explanation, status, tags, created_at, updated_at) "
        "VALUES ('c2', 'React Hooks', 'react', 'Hooks manage state in function components', 'active', '[]', "
        "datetime('now'), datetime('now'))"
    )
    conn.commit()


@pytest.fixture
def garden_client():
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    run_migrations(conn)
    app = create_app()
    app.dependency_overrides[_get_conn] = lambda: conn
    yield TestClient(app), conn
    app.dependency_overrides.clear()
    conn.close()


def test_get_plants_returns_fsrs_and_seed_stages(garden_client):
    client, conn = garden_client
    _seed_data(conn)

    response = client.get("/api/garden/plants")

    assert response.status_code == 200

    body = response.json()
    assert body["success"] is True
    assert body["error"] is None
    assert isinstance(body["data"], list)
    assert len(body["data"]) == 2

    by_id = {p["card_id"]: p for p in body["data"]}
    assert by_id["c1"]["plant_stage"] == "tree"
    assert by_id["c1"]["title"] == "Python Decorators"
    assert by_id["c2"]["plant_stage"] == "seed"
    assert by_id["c2"]["fsrs_state"] is None


def test_get_stats_summarizes_plants_and_due_cards(garden_client):
    client, conn = garden_client
    _seed_data(conn)

    response = client.get("/api/garden/stats")

    assert response.status_code == 200

    body = response.json()
    assert body["success"] is True
    assert body["data"]["total_plants"] == 2
    assert body["data"]["due_count"] == 1
    assert body["data"]["flowering_rate"] == 50.0


def test_get_plants_returns_empty_for_new_garden(garden_client):
    client, _ = garden_client

    response = client.get("/api/garden/plants")

    assert response.status_code == 200
    assert response.json()["data"] == []
