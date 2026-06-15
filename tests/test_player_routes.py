from fastapi.testclient import TestClient

from trowel_py.db.connection import create_db
from trowel_py.db.migrate import run_migrations


def _clean_db() -> None:
    """wipe player state from the real db so tests start clean."""
    conn = create_db()
    run_migrations(conn)
    conn.execute("delete from inventory")
    conn.execute("delete from players")
    conn.commit()
    conn.close()


def _give_coins(coins: int) -> None:
    """insert the default player with a coin balance (for buy-success tests)."""
    conn = create_db()
    conn.execute(
        "insert into players (id, last_active, coins) values ('default', ?, ?)",
        ("2026-06-15T10:00:00", coins),
    )
    conn.commit()
    conn.close()


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
