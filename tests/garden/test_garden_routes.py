import sqlite3

import pytest
from fastapi.testclient import TestClient

from trowel_py.app import create_app
from trowel_py.db.migrate import run_migrations
from trowel_py.garden.routes import _get_conn

# module-level in-memory db shared by the client fixture AND the seed/clean
# helpers, so the two never talk past each other. tests run serial, so one
# shared connection is safe (same pattern as test_m2_e2e). this keeps the
# tests from touching the real trowel.db — which previously got its cards
# wiped on every pytest run.
_db = sqlite3.connect(":memory:", check_same_thread=False)
_db.row_factory = sqlite3.Row
_db.execute("PRAGMA foreign_keys=ON")
run_migrations(_db)


def _seed_data() -> None:
    """insert test cards + fsrs_state into the shared in-memory db."""
    _db.execute(
        "INSERT INTO cards (id, title, category, explanation, status, tags, created_at, updated_at) "
        "VALUES ('c1', 'Python Decorators', 'python', 'Decorators wrap functions', 'active', '[]', "
        "datetime('now'), datetime('now'))"
    )
    _db.execute(
        "INSERT INTO fsrs_state (card_id, state, stability, difficulty, reps, lapses, due) "
        "VALUES ('c1', 2, 5.0, 3.0, 10, 1, '2020-01-01T00:00:00')"
    )
    _db.execute(
        "INSERT INTO cards (id, title, category, explanation, status, tags, created_at, updated_at) "
        "VALUES ('c2', 'React Hooks', 'react', 'Hooks manage state in function components', 'active', '[]', "
        "datetime('now'), datetime('now'))"
    )
    _db.commit()


def _clean_db() -> None:
    """wipe cards + fsrs_state in the shared in-memory db."""
    _db.execute("DELETE FROM fsrs_state")
    _db.execute("DELETE FROM cards")
    _db.commit()


@pytest.fixture
def client() -> TestClient:
    """TestClient wired to the shared in-memory db (does NOT touch trowel.db)."""
    app = create_app()
    app.dependency_overrides[_get_conn] = lambda: _db
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_get_plants(client: TestClient):
    """GET /api/garden/plants 返回正确结构"""
    _clean_db()
    _seed_data()
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


def test_get_stats(client: TestClient):
    """GET /api/garden/stats 返回聚合数据"""
    _clean_db()
    _seed_data()
    response = client.get("/api/garden/stats")
    assert response.status_code == 200

    body = response.json()
    assert body["success"] is True
    assert body["data"]["total_plants"] == 2
    assert body["data"]["due_count"] == 1        # c1 due 在过去
    assert body["data"]["flowering_rate"] == 50.0  # 1/2 * 100


def test_get_plants_empty(client: TestClient):
    """空花园"""
    _clean_db()
    response = client.get("/api/garden/plants")
    assert response.status_code == 200
    assert response.json()["data"] == []
