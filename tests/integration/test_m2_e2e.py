"""通过同一隔离数据库串联游戏主循环，只验证跨领域协作是否成立。"""

import sqlite3

import pytest
from fastapi.testclient import TestClient

from trowel_py.app import create_app
from trowel_py.db.migrate import run_migrations
from trowel_py.cards.routes import _get_conn as cards_conn
from trowel_py.review.routes import _get_conn as review_conn
from trowel_py.player.routes import _get_conn as player_conn
from trowel_py.events.routes import _get_conn as events_conn
from trowel_py.pet.routes import _get_conn as pet_conn
from trowel_py.garden.routes import _get_conn as garden_conn


@pytest.fixture
def game_loop():
    app = create_app()
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    run_migrations(conn)

    # 任一领域漏掉 override 都可能回退真实 trowel.db，必须统一绑定到这条隔离连接。
    for get_conn in (
        cards_conn,
        review_conn,
        player_conn,
        events_conn,
        pet_conn,
        garden_conn,
    ):
        app.dependency_overrides[get_conn] = lambda: conn

    yield TestClient(app), conn

    app.dependency_overrides.clear()
    conn.close()


def _seed_due_card(conn: sqlite3.Connection, card_id: str = "card-1") -> None:
    conn.execute(
        "INSERT INTO cards (id, title, category, explanation, tags, status) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (card_id, "E2E Card", "test", "explanation long enough", "[]", "active"),
    )
    conn.execute(
        "INSERT INTO fsrs_state (card_id, state, reps, due) VALUES (?, ?, ?, ?)",
        (card_id, 0, 0, "2020-01-01T00:00:00"),
    )
    conn.commit()


def _seed_player_with_coins(
    conn: sqlite3.Connection,
    amount: int = 100,
) -> None:
    conn.execute(
        "INSERT INTO players (id, last_active, coins) VALUES ('default', ?, ?)",
        ("2026-06-15T10:00:00", amount),
    )
    conn.commit()


def _inventory_row_id(client: TestClient, catalog_id: str) -> str:
    inv = client.get("/api/player/inventory").json()["data"]
    return next(i["id"] for i in inv if i["item_id"] == catalog_id)


def test_review_grows_plant_and_shop_actions_update_pet(game_loop):
    client, conn = game_loop
    _seed_due_card(conn)
    _seed_player_with_coins(conn, 100)

    profile = client.get("/api/player").json()["data"]
    assert profile["level"] == 1
    assert profile["xp"] == 0
    assert profile["coins"] == 100

    due = client.get("/api/review/due").json()["data"]
    assert len(due) == 1
    submit = client.post(
        "/api/review/submit", json={"card_id": "card-1", "rating": 3}
    ).json()
    assert submit["success"] is True
    assert submit["data"]["plant_changed"] is True

    # XP 只由事件奖励；复习推进 FSRS 和植物状态，不直接增加 XP。
    assert client.get("/api/player").json()["data"]["xp"] == 0

    plants = client.get("/api/garden/plants").json()["data"]
    assert plants[0]["plant_stage"] != "seed"

    buy_food = client.post("/api/player/buy", json={"item_id": "food_basic"}).json()
    assert buy_food["success"] is True
    assert client.get("/api/player").json()["data"]["coins"] == 90

    pet_before = client.get("/api/pet").json()["data"]
    food_row_id = _inventory_row_id(client, "food_basic")
    feed_resp = client.post("/api/pet/feed", json={"item_id": food_row_id}).json()
    assert feed_resp["success"] is True
    pet_after = client.get("/api/pet").json()["data"]
    assert pet_after["hunger"] == pet_before["hunger"] + 20

    inv_after = client.get("/api/player/inventory").json()["data"]
    assert all(i["id"] != food_row_id for i in inv_after)

    buy_hat = client.post("/api/player/buy", json={"item_id": "hat_straw"}).json()
    assert buy_hat["success"] is True
    hat_row_id = _inventory_row_id(client, "hat_straw")
    equip_resp = client.put("/api/pet/equip", json={"item_id": hat_row_id}).json()
    assert equip_resp["success"] is True

    pet = client.get("/api/pet").json()["data"]
    assert pet["equipped_hat"] == hat_row_id


def test_event_trigger_endpoint_and_history(game_loop):
    client, conn = game_loop
    _seed_due_card(conn)
    _seed_player_with_coins(conn, 100)

    # 路由内部创建随机源，这里只验证端点与 history 契约；具体冷却和奖励由 service 测试覆盖。
    for _ in range(3):
        resp = client.post("/api/events/trigger").json()
        assert resp["success"] is True
        assert resp["error"] is None

    history = client.get("/api/events/history").json()
    assert history["success"] is True
    assert isinstance(history["data"], list)
