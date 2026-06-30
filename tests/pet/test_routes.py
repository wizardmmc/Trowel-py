"""pet routes smoke tests — endpoints alive, response shape, key side effects.

in-memory db + _clean_db isolation (same pattern as test_m2_e2e). does NOT
touch the real trowel.db.

NOTE: POST /feed, POST /interact, PUT /equip are NOT idempotent. these tests
assert SIDE EFFECTS (food consumed, mood changed, hat swapped), not that
repeating the call yields the same result. only GET /api/pet is idempotent.
"""
from __future__ import annotations

import sqlite3

import pytest
from fastapi.testclient import TestClient

from trowel_py.app import create_app
from trowel_py.db.migrate import run_migrations
from trowel_py.pet.routes import _get_conn as pet_conn
from trowel_py.player.routes import _get_conn as player_conn

# module-level in-memory db shared across the client fixture and the helpers.
# pet tests also read /api/player/inventory (to assert food was consumed), so
# BOTH pet and player _get_conn are overridden to this same in-memory db —
# otherwise the feed (pet) and the inventory check (player) would hit different
# databases and the side-effect assertion would flake.
_db = sqlite3.connect(":memory:", check_same_thread=False)
_db.row_factory = sqlite3.Row
_db.execute("PRAGMA foreign_keys=ON")
run_migrations(_db)


def _clean_db() -> None:
    """wipe pet/player/inventory state, then re-seed the default player.

    the default player must exist because pets.player_id FKs players(id) —
    create_pet_repository's find_or_create inserts a pet row, which would otherwise
    raise IntegrityError.
    """
    _db.execute("delete from pets")
    _db.execute("delete from inventory")
    _db.execute("delete from players")
    _db.execute(
        "insert into players (id, last_active) values ('default', ?)",
        ("2026-06-15T10:00:00",),
    )
    _db.commit()


def _seed_item(row_id: str, catalog: str, item_type: str) -> None:
    """add one inventory row with a known id (player already seeded by _clean_db)."""
    _db.execute(
        "insert into inventory (id, player_id, item_id, item_type) "
        "values (?, 'default', ?, ?)",
        (row_id, catalog, item_type),
    )
    _db.commit()


@pytest.fixture
def client() -> TestClient:
    """TestClient wired to the shared in-memory db (does NOT touch trowel.db)."""
    app = create_app()
    app.dependency_overrides[pet_conn] = lambda: _db
    app.dependency_overrides[player_conn] = lambda: _db
    yield TestClient(app)
    app.dependency_overrides.clear()


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
