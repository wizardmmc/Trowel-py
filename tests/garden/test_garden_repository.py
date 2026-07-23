import sqlite3
from trowel_py.db.migrate import run_migrations
from trowel_py.garden.repository import create_garden_repository


def _insert_card(conn: sqlite3.Connection, card_id: str, category: str = "python") -> None:
    """helper: 直接插一张卡片"""
    conn.execute(
        "INSERT INTO cards (id, title, category, explanation, status, tags, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, 'active', '[]', datetime('now'), datetime('now'))",
        (card_id, f"Title of {card_id}", category, f"Explanation for {card_id} is long enough"),
    )


def _insert_fsrs(conn: sqlite3.Connection, card_id: str, state: int, due: str) -> None:
    """helper: 插一条 fsrs_state"""
    conn.execute(
        "INSERT INTO fsrs_state (card_id, state, stability, difficulty, reps, lapses, due) "
        "VALUES (?, ?, 1.0, 1.0, 3, 0, ?)",
        (card_id, state, due),
    )


# ---- 视角 1: 正常路径 ----

def test_get_all_plants_with_fsrs(db_connection: sqlite3.Connection):
    """有 fsrs_state 的卡片，LEFT JOIN 返回完整数据"""
    run_migrations(db_connection)
    _insert_card(db_connection, "c1")
    _insert_fsrs(db_connection, "c1", state=2, due="2026-12-01T00:00:00+00:00")

    repo = create_garden_repository(db_connection)
    results = repo.get_all_plants()

    assert len(results) == 1
    row = results[0]
    assert row["id"] == "c1"
    assert row["state"] == 2
    assert row["reps"] == 3
    assert row["due"] is not None


# ---- 视角 2: 边界值 - 空花园 ----

def test_get_all_plants_empty(db_connection: sqlite3.Connection):
    """没有任何卡片，返回空列表"""
    run_migrations(db_connection)
    repo = create_garden_repository(db_connection)
    results = repo.get_all_plants()
    assert results == []


# ---- 视角 3: LEFT JOIN 边界 - 有 + 没有 fsrs_state 的混合 ----

def test_get_all_plants_mixed_fsrs(db_connection: sqlite3.Connection):
    """
    LEFT JOIN 核心测试：两张卡片，一张有 fsrs_state，一张没有。
    没有 fsrs_state 的卡片不能丢，字段应该是 None。
    """
    run_migrations(db_connection)
    _insert_card(db_connection, "c1")
    _insert_card(db_connection, "c2")
    _insert_fsrs(db_connection, "c1", state=2, due="2026-12-01T00:00:00+00:00")
    # c2 没有 fsrs_state

    repo = create_garden_repository(db_connection)
    results = repo.get_all_plants()

    assert len(results) == 2
    by_id = {r["id"]: r for r in results}
    assert by_id["c1"]["state"] == 2
    assert by_id["c1"]["due"] is not None
    assert by_id["c2"]["state"] is None       # LEFT JOIN NULL
    assert by_id["c2"]["due"] is None         # LEFT JOIN NULL
    assert by_id["c2"]["reps"] is None        # LEFT JOIN NULL


# ---- 视角 4: 聚合计算验证 - stats ----

def test_get_stats_mixed_states(db_connection: sqlite3.Connection):
    """
    插入 3 张卡片：
    - c1: state=2(tree), due 在未来 → 开花但不用浇水
    - c2: state=1(sprout), due 在过去 → 不开花但需要浇水
    - c3: state=2(tree), due 在过去 → 开花且需要浇水
    验证: total=3, due_count=2, flowering_rate=66.7%
    """
    run_migrations(db_connection)
    past = "2020-01-01T00:00:00+00:00"
    future = "2099-01-01T00:00:00+00:00"

    _insert_card(db_connection, "c1")
    _insert_card(db_connection, "c2")
    _insert_card(db_connection, "c3")
    _insert_fsrs(db_connection, "c1", state=2, due=future)
    _insert_fsrs(db_connection, "c2", state=1, due=past)
    _insert_fsrs(db_connection, "c3", state=2, due=past)

    repo = create_garden_repository(db_connection)
    stats = repo.get_stats()

    assert stats["total_plants"] == 3
    assert stats["due_count"] == 2       # c2 + c3
    assert stats["flowering_rate"] == 66.7  # 2/3 * 100


def test_get_stats_empty(db_connection: sqlite3.Connection):
    """空花园，全是 0"""
    run_migrations(db_connection)
    repo = create_garden_repository(db_connection)
    stats = repo.get_stats()

    assert stats["total_plants"] == 0
    assert stats["due_count"] == 0
    assert stats["flowering_rate"] == 0.0


def test_get_stats_no_fsrs(db_connection: sqlite3.Connection):
    """有卡片但没有 fsrs_state，due_count=0，flowering_rate=0.0"""
    run_migrations(db_connection)
    _insert_card(db_connection, "c1")

    repo = create_garden_repository(db_connection)
    stats = repo.get_stats()

    assert stats["total_plants"] == 1
    assert stats["due_count"] == 0
    assert stats["flowering_rate"] == 0.0
