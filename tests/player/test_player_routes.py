import sqlite3

import pytest
from fastapi.testclient import TestClient

from trowel_py.app import create_app
from trowel_py.db.migrate import run_migrations
from trowel_py.player.routes import _get_conn


def _give_coins(conn: sqlite3.Connection, coins: int) -> None:
    conn.execute(
        "insert into players (id, last_active, coins) values ('default', ?, ?)",
        ("2026-06-15T10:00:00", coins),
    )
    conn.commit()


@pytest.fixture
def player_client():
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    run_migrations(conn)
    app = create_app()
    app.dependency_overrides[_get_conn] = lambda: conn
    yield TestClient(app), conn
    app.dependency_overrides.clear()
    conn.close()


def test_get_player_returns_default_profile(player_client):
    client, _ = player_client

    resp = client.get("/api/player")

    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    assert body["error"] is None
    assert body["data"]["id"] == "default"
    assert body["data"]["level"] == 1
    assert body["data"]["xp"] == 0
    assert body["data"]["coins"] == 0


def test_get_inventory_returns_empty_for_new_player(player_client):
    client, _ = player_client

    resp = client.get("/api/player/inventory")

    assert resp.status_code == 200
    assert resp.json()["data"] == []


def test_buy_reports_insufficient_coins(player_client):
    client, _ = player_client

    resp = client.post("/api/player/buy", json={"item_id": "food_basic"})

    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is False
    assert "coins" in body["error"]


def test_buy_rejects_missing_item_id(player_client):
    client, _ = player_client

    resp = client.post("/api/player/buy", json={})

    assert resp.status_code == 422


def test_buy_persists_inventory_and_deducts_coins(player_client):
    client, conn = player_client
    _give_coins(conn, 100)

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
