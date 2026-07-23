from __future__ import annotations

import sqlite3

import pytest
from fastapi.testclient import TestClient

from trowel_py.app import create_app
from trowel_py.db.migrate import run_migrations
from trowel_py.pet.routes import _get_conn as pet_conn
from trowel_py.player.repository import create_player_repository
from trowel_py.player.routes import _get_conn as player_conn


def _add_inventory_item(
    conn: sqlite3.Connection,
    row_id: str,
    catalog_id: str,
    item_type: str,
) -> None:
    conn.execute(
        "insert into inventory (id, player_id, item_id, item_type) "
        "values (?, 'default', ?, ?)",
        (row_id, catalog_id, item_type),
    )
    conn.commit()


@pytest.fixture
def pet_client():
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    run_migrations(conn)
    create_player_repository(conn).find_or_create()

    app = create_app()
    # 喂食会同时修改 pet 和 player 领域，两套路由必须共享同一条隔离连接。
    app.dependency_overrides[pet_conn] = lambda: conn
    app.dependency_overrides[player_conn] = lambda: conn
    client = TestClient(app)
    try:
        yield client, conn
    finally:
        client.close()
        app.dependency_overrides.clear()
        conn.close()


def test_get_pet_returns_defaults(pet_client):
    client, _ = pet_client

    response = client.get("/api/pet")

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["data"]["mood"] == "normal"
    assert body["data"]["hunger"] == 80
    assert body["data"]["equipped_hat"] is None


def test_interact_returns_line_and_happy_pet(pet_client):
    client, _ = pet_client

    response = client.post("/api/pet/interact")

    assert response.status_code == 200
    body = response.json()["data"]
    assert body["response"]["mood"] == "happy"
    assert body["response"]["text"]
    assert body["pet"]["mood"] == "happy"


def test_feed_consumes_food_and_restores_hunger(pet_client):
    client, conn = pet_client
    _add_inventory_item(conn, "food-1", "food_basic", "food")

    response = client.post("/api/pet/feed", json={"item_id": "food-1"})

    assert response.status_code == 200
    assert response.json()["success"] is True
    assert client.get("/api/pet").json()["data"]["hunger"] == 100
    assert client.get("/api/player/inventory").json()["data"] == []


def test_feed_unknown_item_returns_user_error(pet_client):
    client, _ = pet_client

    response = client.post("/api/pet/feed", json={"item_id": "ghost"})

    assert response.status_code == 200
    assert response.json()["success"] is False


def test_feed_rejects_missing_item_id(pet_client):
    client, _ = pet_client

    response = client.post("/api/pet/feed", json={})

    assert response.status_code == 422


def test_equip_sets_inventory_row_as_current_hat(pet_client):
    client, conn = pet_client
    _add_inventory_item(conn, "hat-1", "hat_straw", "hat")

    response = client.put("/api/pet/equip", json={"item_id": "hat-1"})

    assert response.status_code == 200
    assert client.get("/api/pet").json()["data"]["equipped_hat"] == "hat-1"
