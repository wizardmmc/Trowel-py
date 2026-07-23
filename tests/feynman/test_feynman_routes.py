import sqlite3
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from trowel_py.app import create_app
from trowel_py.db.migrate import run_migrations
from trowel_py.feynman.routes import _get_conn, _get_llm_service
from trowel_py.schemas.feynman import FeynmanEvaluationSchema, FeynmanQuestionSchema


def _seed_card(conn: sqlite3.Connection, card_id: str = "card-1") -> None:
    conn.execute(
        "insert into cards (id, title, category, explanation, example, tags) "
        "values (?, ?, ?, ?, ?, ?)",
        (
            card_id,
            "useEffect",
            "React",
            "runs side effects after render",
            "useEffect(() => { ... }, [])",
            '["hooks"]',
        ),
    )


def _seed_session(
    conn: sqlite3.Connection,
    session_id: str,
    card_id: str = "card-1",
    question: str = "why?",
) -> None:
    conn.execute(
        "insert into feynman_sessions (id, card_id, question) values (?, ?, ?)",
        (session_id, card_id, question),
    )


def _fake_question_llm() -> MagicMock:
    llm = MagicMock()
    llm.structured_call.return_value = FeynmanQuestionSchema(
        question="when does cleanup run?", hint="think unmount"
    )
    return llm


def _fake_eval_llm() -> MagicMock:
    llm = MagicMock()
    llm.structured_call.return_value = FeynmanEvaluationSchema(
        accuracy=80,
        completeness=60,
        feedback="missed unmount",
        missed_points=["unmount case"],
    )
    return llm


@pytest.fixture
def feynman_app():
    # FastAPI 在线程池执行同步路由；TestClient 串行访问，因此允许内存连接跨线程复用。
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    run_migrations(conn)
    _seed_card(conn)

    def _override_conn():
        yield conn

    app = create_app()
    app.dependency_overrides[_get_conn] = _override_conn
    yield app, conn
    app.dependency_overrides.clear()
    conn.close()


def test_generate_returns_question_envelope_and_session_id(feynman_app):
    app, _ = feynman_app
    app.dependency_overrides[_get_llm_service] = _fake_question_llm
    client = TestClient(app)

    resp = client.post("/api/feynman/generate", json={"card_id": "card-1"})

    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    assert body["error"] is None
    assert body["data"]["question"] == "when does cleanup run?"
    assert body["data"]["hint"] == "think unmount"
    assert body["data"]["session_id"]


def test_generate_422_on_empty_card_id(feynman_app):
    app, _ = feynman_app
    app.dependency_overrides[_get_llm_service] = _fake_question_llm
    client = TestClient(app)

    resp = client.post("/api/feynman/generate", json={"card_id": ""})

    assert resp.status_code == 422
    assert "detail" in resp.json()


def test_generate_error_envelope_when_card_missing(feynman_app):
    app, _ = feynman_app
    app.dependency_overrides[_get_llm_service] = _fake_question_llm
    client = TestClient(app)

    resp = client.post("/api/feynman/generate", json={"card_id": "ghost-card"})

    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is False
    assert body["data"] is None
    assert body["error"] == "Card not found"


def test_evaluate_returns_scores_and_missed_points(feynman_app):
    app, conn = feynman_app
    _seed_session(conn, "s-1", question="when does cleanup run?")
    app.dependency_overrides[_get_llm_service] = _fake_eval_llm
    client = TestClient(app)

    resp = client.post(
        "/api/feynman/evaluate",
        json={"session_id": "s-1", "answer": "on unmount"},
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    assert body["data"]["accuracy"] == 80
    assert body["data"]["completeness"] == 60
    assert body["data"]["missed_points"] == ["unmount case"]


def test_evaluate_error_envelope_when_session_missing(feynman_app):
    app, _ = feynman_app
    app.dependency_overrides[_get_llm_service] = _fake_eval_llm
    client = TestClient(app)

    resp = client.post(
        "/api/feynman/evaluate",
        json={"session_id": "ghost", "answer": "x"},
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is False
    assert body["error"] == "Session not found"


def test_history_returns_sessions_newest_first(feynman_app):
    app, conn = feynman_app
    _seed_session(conn, "s-1", question="old")
    conn.execute(
        "UPDATE feynman_sessions SET created_at = '2026-01-01 10:00:00' WHERE id = 's-1'"
    )
    _seed_session(conn, "s-2", question="new")
    conn.execute(
        "UPDATE feynman_sessions SET created_at = '2026-01-02 10:00:00' WHERE id = 's-2'"
    )
    client = TestClient(app)

    resp = client.get("/api/feynman/history/card-1")

    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    ids = [s["id"] for s in body["data"]]
    assert ids == ["s-2", "s-1"], "newest first"


def test_history_empty_for_card_with_no_sessions(feynman_app):
    app, _ = feynman_app
    client = TestClient(app)

    resp = client.get("/api/feynman/history/card-1")

    assert resp.status_code == 200
    assert resp.json()["data"] == []
