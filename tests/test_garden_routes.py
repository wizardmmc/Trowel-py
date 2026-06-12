from fastapi.testclient import TestClient
from trowel_py.app import create_app
from trowel_py.db.connection import create_db
from trowel_py.db.migrate import run_migrations


def _seed_data() -> None:
    """往真实数据库里插测试数据"""
    conn = create_db()
    run_migrations(conn)
    # c1: 有 fsrs_state, state=2 (tree), due 在过去
    conn.execute(
        "INSERT INTO cards (id, title, category, explanation, status, tags, created_at, updated_at) "
        "VALUES ('c1', 'Python Decorators', 'python', 'Decorators wrap functions', 'active', '[]', "
        "datetime('now'), datetime('now'))"
    )
    conn.execute(
        "INSERT INTO fsrs_state (card_id, state, stability, difficulty, reps, lapses, due) "
        "VALUES ('c1', 2, 5.0, 3.0, 10, 1, '2020-01-01T00:00:00')"
    )
    # c2: 没有 fsrs_state
    conn.execute(
        "INSERT INTO cards (id, title, category, explanation, status, tags, created_at, updated_at) "
        "VALUES ('c2', 'React Hooks', 'react', 'Hooks manage state in function components', 'active', '[]', "
        "datetime('now'), datetime('now'))"
    )
    conn.commit()
    conn.close()


def _clean_db() -> None:
    """清空真实数据库"""
    conn = create_db()
    run_migrations(conn)
    conn.execute("DELETE FROM fsrs_state")
    conn.execute("DELETE FROM cards")
    conn.commit()
    conn.close()


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
