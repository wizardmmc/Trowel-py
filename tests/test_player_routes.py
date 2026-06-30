import sqlite3

import pytest
from fastapi.testclient import TestClient

from trowel_py.app import create_app
from trowel_py.db.migrate import run_migrations
from trowel_py.player.routes import _get_conn

# module-level in-memory db shared by the client fixture AND the seed/clean
# helpers, so seeds and HTTP requests never talk past each other. tests run
# serial, so one shared connection is safe (same pattern as test_m2_e2e).
# this keeps the tests from touching the real trowel.db — which previously
# got its player/inventory state wiped on every pytest run.
_db = sqlite3.connect(":memory:", check_same_thread=False)
_db.row_factory = sqlite3.Row
_db.execute("PRAGMA foreign_keys=ON")
run_migrations(_db)


def _clean_db() -> None:
    """wipe player state so tests start clean."""
    _db.execute("delete from inventory")
    _db.execute("delete from players")
    _db.commit()


def _give_coins(coins: int) -> None:
    """insert the default player with a coin balance (for buy-success tests)."""
    _db.execute(
        "insert into players (id, last_active, coins) values ('default', ?, ?)",
        ("2026-06-15T10:00:00", coins),
    )
    _db.commit()


@pytest.fixture
def client() -> TestClient:
    """TestClient wired to the shared in-memory db (does NOT touch trowel.db)."""
    app = create_app()
    app.dependency_overrides[_get_conn] = lambda: _db
    yield TestClient(app)
    app.dependency_overrides.clear()


# ---- GET /api/player ----

def test_get_player_returns_profile(client: TestClient):
    _clean_db()
    resp = client.get("/api/player")

    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    assert body["error"] is None
    assert body["data"]["id"] == "default"
    assert body["data"]["level"] == 1
    assert body["data"]["xp"] == 0
    assert body["data"]["coins"] == 0


# ---- GET /api/player/inventory ----

def test_get_inventory_empty(client: TestClient):
    _clean_db()
    resp = client.get("/api/player/inventory")

    assert resp.status_code == 200
    assert resp.json()["data"] == []


# ---- POST /api/player/buy ----

def test_buy_insufficient_coins(client: TestClient):
    """fresh player has 0 coins -> buy fails gracefully (not a 500)."""
    _clean_db()
    resp = client.post("/api/player/buy", json={"item_id": "food_basic"})

    assert resp.status_code == 200              # success:False, not 500
    body = resp.json()
    assert body["success"] is False
    assert "coins" in body["error"]


def test_buy_invalid_body_rejected(client: TestClient):
    """missing item_id -> FastAPI returns 422 before the handler runs."""
    _clean_db()
    resp = client.post("/api/player/buy", json={})
    assert resp.status_code == 422


def test_buy_success_e2e(client: TestClient):
    """seed coins -> buy -> inventory grows and coins drop, all via HTTP."""
    _clean_db()
    _give_coins(100)

    resp = client.post("/api/player/buy", json={"item_id": "food_basic"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    assert body["data"]["item_type"] == "food"

    inventory = client.get("/api/player/inventory").json()["data"]
    assert len(inventory) == 1
    assert inventory[0]["item_id"] == "food_basic"

    profile = client.get("/api/player").json()["data"]
    assert profile["coins"] == 90
