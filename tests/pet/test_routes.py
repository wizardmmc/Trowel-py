"""pet routes smoke tests — endpoints alive, response shape, key side effects.

uses the real trowel.db + _clean_db (same isolation style as test_player_routes
/ test_garden_routes).

NOTE: POST /feed, POST /interact, PUT /equip are NOT idempotent. these tests
assert SIDE EFFECTS (food consumed, mood changed, hat swapped), not that
repeating the call yields the same result. only GET /api/pet is idempotent.
"""
from __future__ import annotations

from fastapi.testclient import TestClient

from trowel_py.db.connection import create_db
from trowel_py.db.migrate import run_migrations


def _clean_db() -> None:
    """wipe pet/player/inventory state, then re-seed the default player.

    the default player must exist because pets.player_id FKs players(id) —
    create_pet_repository's find_or_create inserts a pet row, which would otherwise
    raise IntegrityError.
    """
    conn = create_db()
    run_migrations(conn)
    conn.execute("delete from pets")
    conn.execute("delete from inventory")
    conn.execute("delete from players")
    conn.execute(
        "insert into players (id, last_active) values ('default', ?)",
        ("2026-06-15T10:00:00",),
    )
    conn.commit()
    conn.close()


def _seed_item(row_id: str, catalog: str, item_type: str) -> None:
    """add one inventory row with a known id (player already seeded by _clean_db)."""
    conn = create_db()
    conn.execute(
        "insert into inventory (id, player_id, item_id, item_type) "
        "values (?, 'default', ?, ?)",
        (row_id, catalog, item_type),
    )
    conn.commit()
    conn.close()


# ---- GET /api/pet (idempotent) ----

def test_get_pet_returns_defaults(client: TestClient):
    _clean_db()
    resp = client.get("/api/pet")
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    assert body["data"]["mood"] == "normal"
    assert body["data"]["hunger"] == 80
    assert body["data"]["equipped_hat"] is None


# ---- POST /api/pet/interact (side effect: mood -> happy) ----

def test_interact_returns_line_and_happy_pet(client: TestClient):
    _clean_db()
    resp = client.post("/api/pet/interact")
    assert resp.status_code == 200
    body = resp.json()["data"]
    assert body["response"]["mood"] == "happy"
    assert body["response"]["text"]  # non-empty
    assert body["pet"]["mood"] == "happy"


# ---- POST /api/pet/feed (side effect: food consumed + hunger up) ----

def test_feed_success_side_effect(client: TestClient):
    # POST is NOT idempotent — assert the food was actually consumed
    _clean_db()
    _seed_item("food-1", "food_basic", "food")

    resp = client.post("/api/pet/feed", json={"item_id": "food-1"})
    assert resp.status_code == 200
    assert resp.json()["success"] is True
    # default hunger 80 + 20 = 100
    assert client.get("/api/pet").json()["data"]["hunger"] == 100
    # food gone from inventory
    assert client.get("/api/player/inventory").json()["data"] == []


def test_feed_unknown_item_returns_user_error_not_500(client: TestClient):
    _clean_db()
    resp = client.post("/api/pet/feed", json={"item_id": "ghost"})
    assert resp.status_code == 200  # success:False, not a server fault
    assert resp.json()["success"] is False


def test_feed_invalid_body_rejected(client: TestClient):
    _clean_db()
    resp = client.post("/api/pet/feed", json={})
    assert resp.status_code == 422  # FastAPI body validation


# ---- PUT /api/pet/equip (side effect: equipped_hat set) ----

def test_equip_success_side_effect(client: TestClient):
    _clean_db()
    _seed_item("hat-1", "hat_straw", "hat")

    resp = client.put("/api/pet/equip", json={"item_id": "hat-1"})
    assert resp.status_code == 200
    assert client.get("/api/pet").json()["data"]["equipped_hat"] == "hat-1"
