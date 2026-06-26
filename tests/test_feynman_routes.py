"""Tests for feynman routes (slice 019).

Top of the pyramid: fewer tests. TestClient hits real HTTP; _get_conn is
overridden to a shared in-memory db (seeded), _get_llm_service to a fake LLM.
"""
import sqlite3
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from trowel_py.app import create_app
from trowel_py.db.migrate import run_migrations
from trowel_py.feynman.routes import _get_conn, _get_llm_service
from trowel_py.schemas.feynman import FeynmanEvaluationSchema, FeynmanQuestionSchema


def _seed_card(conn: sqlite3.Connection, card_id: str = "card-1") -> None:
    """insert a card with content so generate has something to read."""
    conn.execute(
        "insert into cards (id, title, category, explanation, example, tags) "
        "values (?, ?, ?, ?, ?, ?)",
        (card_id, "useEffect", "React", "runs side effects after render",
         "useEffect(() => { ... }, [])", '["hooks"]'),
    )


def _seed_session(
    conn: sqlite3.Connection,
    session_id: str,
    card_id: str = "card-1",
    question: str = "why?",
) -> None:
    """insert a question-stage session directly, bypassing the service."""
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
        accuracy=80, completeness=60,
        feedback="missed unmount", missed_points=["unmount case"],
    )
    return llm


@pytest.fixture
def feynman_app():
    """app wired to an in-memory db seeded with a card; LLM overridden per-test."""
    # check_same_thread=False: FastAPI runs sync routes in a threadpool, so the
    # conn is touched from a different thread than the one that created it.
    # safe here because TestClient requests are serial (no concurrent access).
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


def test_generate_happy_path(feynman_app):
    """POST /generate -> 200 envelope with question + hint + session_id."""
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
    """empty card_id rejected at schema layer (min_length=1) -> 422."""
    app, _ = feynman_app
    app.dependency_overrides[_get_llm_service] = _fake_question_llm
    client = TestClient(app)

    resp = client.post("/api/feynman/generate", json={"card_id": ""})

    assert resp.status_code == 422
    assert "detail" in resp.json()


def test_generate_error_envelope_when_card_missing(feynman_app):
    """non-existent card -> success=False error envelope (service returned None)."""
    app, _ = feynman_app
    app.dependency_overrides[_get_llm_service] = _fake_question_llm
    client = TestClient(app)

    resp = client.post("/api/feynman/generate", json={"card_id": "ghost-card"})

    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is False
    assert body["data"] is None
    assert body["error"] == "Card not found"


def test_evaluate_happy_path(feynman_app):
    """POST /evaluate -> 200 envelope with the scores."""
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
    """non-existent session -> success=False error envelope."""
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
    """GET /history/{card_id} -> sessions ordered newest first; no LLM call."""
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
    """GET /history -> [] when the card has no sessions."""
    app, _ = feynman_app
    client = TestClient(app)

    resp = client.get("/api/feynman/history/card-1")

    assert resp.status_code == 200
    assert resp.json()["data"] == []
