"""M2 slice 017 — end-to-end game loop verification.

把整条游戏循环通过真实 HTTP routes 串起来跑一遍，验证 review / player / pet /
events / garden 五个模块能通过同一个 DB 协同工作。单个模块的正确性各自由
test_player / test_events / test_pet / test_review 覆盖；这个文件只验证
"拼在一起能不能转"。

与原版 ts slice 017 的分叉（重要）:
- 复习不直接加 XP: py 版 submit_review 只更新 fsrs_state + review_log,
  XP 来自事件奖励(不是复习)。所以"复习 -> XP 涨"这条 AC 在 py 版不存在,
  这里验证的是等价的"复习 -> 植物成长(plant_changed)"。
- SSE 推送: 016 已判定 py 版不搭 SSE(方案 B), 这条 AC 划掉。
- player 初始 coins=0: buy -> feed 链需要先有金币, 而金币来自随机事件。
  测试 setup 直接给 player 充值, 聚焦验证 buy->feed->equip 链路本身
  (金币获取属事件层, service 层测试已覆盖)。
- trigger 随机: routes 层 trigger 的随机源写死(random.Random()), 无法稳定
  触发特定事件。这里对 trigger 做弱断言(能调通 + history 落库), 冷却/奖励
  的确定性验证留给 service 层 test_events_cooldown / test_rewards。
"""
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
def e2e():
    """
    内存 DB, 通过 dependency_overrides 让所有模块的 _get_conn 返回同一个 conn。
    跨模块协同的关键: review/player/pet/events/garden 共享同一份内存数据。
    """
    app = create_app()
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    run_migrations(conn)

    for get_conn in (cards_conn, review_conn, player_conn, events_conn, pet_conn, garden_conn):
        app.dependency_overrides[get_conn] = lambda: conn

    yield TestClient(app), conn

    app.dependency_overrides.clear()
    conn.close()


def _seed_due_card(conn: sqlite3.Connection, card_id: str = "card-1") -> None:
    """插一张到期且从未复习的卡片, 供复习 / 事件使用。"""
    conn.execute(
        "INSERT INTO cards (id, title, category, explanation, tags, status) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (card_id, "E2E Card", "test", "explanation long enough", "[]", "active"),
    )
    conn.execute(
        "INSERT INTO fsrs_state (card_id, state, reps, due) VALUES (?, ?, ?, ?)",
        (card_id, 0, 0, "2020-01-01T00:00:00"),  # 过期 + 从未复习
    )
    conn.commit()


def _give_coins(conn: sqlite3.Connection, amount: int = 100) -> None:
    """给 default player 充金币 (模拟"玩家已有金币", 聚焦 buy->feed 链路)。"""
    conn.execute(
        "INSERT INTO players (id, last_active, coins) VALUES ('default', ?, ?)",
        ("2026-06-15T10:00:00", amount),
    )
    conn.commit()


def _find_row_id(client: TestClient, catalog_id: str) -> str:
    """从背包找一个 catalog id 对应的 inventory row id (buy 不返回 row id)。"""
    inv = client.get("/api/player/inventory").json()["data"]
    return next(i["id"] for i in inv if i["item_id"] == catalog_id)


def test_full_game_loop_review_buy_feed_equip(e2e):
    """主干循环: 复习->植物成长, 买食物->喂宠物->饱腹恢复, 买帽子->装备联动。"""
    client, conn = e2e
    _seed_due_card(conn)
    _give_coins(conn, 100)

    # 1. 初始状态: level 1, 0 xp, 100 coins
    profile = client.get("/api/player").json()["data"]
    assert profile["level"] == 1
    assert profile["xp"] == 0
    assert profile["coins"] == 100

    # 2. 复习一张到期卡 -> 植物成长 (py 版: 复习不加 XP, 只长植物)
    due = client.get("/api/review/due").json()["data"]
    assert len(due) == 1
    submit = client.post("/api/review/submit", json={"card_id": "card-1", "rating": 3}).json()
    assert submit["success"] is True
    assert submit["data"]["plant_changed"] is True  # seed -> sprout/tree

    # 复习后 XP 仍为 0 (py 版设计: XP 来自事件而非复习)
    assert client.get("/api/player").json()["data"]["xp"] == 0

    # 3. 花园里那株植物确实长大了
    plants = client.get("/api/garden/plants").json()["data"]
    assert plants[0]["plant_stage"] != "seed"

    # 4. 买食物 -> 金币 -10, 背包多一行
    buy_food = client.post("/api/player/buy", json={"item_id": "food_basic"}).json()
    assert buy_food["success"] is True
    assert client.get("/api/player").json()["data"]["coins"] == 90  # 100 - 10

    # 5. 喂宠物 -> 饱腹度(hunger)恢复 +20, 食物被消耗
    pet_before = client.get("/api/pet").json()["data"]
    food_row_id = _find_row_id(client, "food_basic")
    feed_resp = client.post("/api/pet/feed", json={"item_id": food_row_id}).json()
    assert feed_resp["success"] is True
    pet_after = client.get("/api/pet").json()["data"]
    assert pet_after["hunger"] == pet_before["hunger"] + 20  # food_basic 恢复 20

    inv_after = client.get("/api/player/inventory").json()["data"]
    assert all(i["id"] != food_row_id for i in inv_after)  # 食物已消耗

    # 6. 买帽子 -> 装备 -> pet.equipped_hat 联动 (row id)
    buy_hat = client.post("/api/player/buy", json={"item_id": "hat_straw"}).json()
    assert buy_hat["success"] is True
    hat_row_id = _find_row_id(client, "hat_straw")
    equip_resp = client.put("/api/pet/equip", json={"item_id": hat_row_id}).json()
    assert equip_resp["success"] is True

    pet = client.get("/api/pet").json()["data"]
    assert pet["equipped_hat"] == hat_row_id  # 装备联动正确


def test_event_trigger_endpoint_and_history(e2e):
    """
    事件触发端点可调通 + history 落库。trigger 随机, 弱断言(不强求具体事件类型);
    冷却 / 奖励的确定性验证留给 service 层测试。
    """
    client, conn = e2e
    _seed_due_card(conn)
    _give_coins(conn, 100)

    # trigger 可重复调用, 每次都 success (data 可能 null=冷却/无事件, 或非 null)
    for _ in range(3):
        resp = client.post("/api/events/trigger").json()
        assert resp["success"] is True
        assert resp["error"] is None

    # history 端点可查, 返回列表
    history = client.get("/api/events/history").json()
    assert history["success"] is True
    assert isinstance(history["data"], list)
