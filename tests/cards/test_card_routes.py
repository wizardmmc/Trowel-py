import sqlite3
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from trowel_py.app import create_app
from trowel_py.cards.routes import _get_conn, _get_llm_service
from trowel_py.db.migrate import run_migrations
from trowel_py.llm.client import LLMService
from trowel_py.schemas.extracted_card import ExtractedCard, ExtractOutput


@pytest.fixture
def app_with_db():
    app = create_app()

    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    run_migrations(conn)

    app.dependency_overrides[_get_conn] = lambda: conn
    app.dependency_overrides[_get_llm_service] = lambda: _mock_llm_service()
    yield app, conn
    conn.close()
    app.dependency_overrides.clear()


@pytest.fixture
def client(app_with_db):
    app, _ = app_with_db
    return TestClient(app)


def _mock_llm_service():
    mock = MagicMock(spec=LLMService)
    mock.structured_call.return_value = ExtractOutput(
        cards=[
            ExtractedCard(
                title="Python Decorators",
                category="Python",
                explanation="A decorator wraps a function to extend its behavior without modifying it",
                tags=["python"],
                confidence=4,
                source_type="git_diff",
            ),
        ]
    )
    return mock


class TestExtract:
    def test_extract_returns_drafts(self, client):
        resp = client.post("/api/cards/extract", json={"content": "some diff text"})

        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert len(body["data"]["drafts"]) == 1
        assert body["data"]["drafts"][0]["title"] == "Python Decorators"

    def test_extract_empty_content_rejected(self, client):
        resp = client.post("/api/cards/extract", json={"content": ""})
        assert resp.status_code == 422


class TestReview:
    def _extract_draft(self, client):
        resp = client.post("/api/cards/extract", json={"content": "some diff"})
        return resp.json()["data"]["drafts"][0]["id"]

    def test_accept_creates_card(self, client, app_with_db):
        _, conn = app_with_db
        draft_id = self._extract_draft(client)

        resp = client.post(f"/api/cards/{draft_id}/review", json={"action": "accept"})

        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert body["data"]["card"]["title"] == "Python Decorators"
        assert body["data"]["card"]["status"] == "active"

        cards = conn.execute("SELECT * FROM cards").fetchall()
        assert len(cards) == 1
        fsrs = conn.execute("SELECT * FROM fsrs_state").fetchall()
        assert len(fsrs) == 1

    def test_reject_no_card_created(self, client, app_with_db):
        _, conn = app_with_db
        draft_id = self._extract_draft(client)

        resp = client.post(f"/api/cards/{draft_id}/review", json={"action": "reject"})

        assert resp.status_code == 200
        body = resp.json()
        assert body["data"]["rejected"] is True

        cards = conn.execute("SELECT * FROM cards").fetchall()
        assert len(cards) == 0
        fsrs = conn.execute("SELECT * FROM fsrs_state").fetchall()
        assert len(fsrs) == 0

    def test_edit_applies_changes_before_creating_card(self, client):
        draft_id = self._extract_draft(client)

        resp = client.post(
            f"/api/cards/{draft_id}/review",
            json={
                "action": "edit",
                "edits": {"title": "Decorators in Python"},
            },
        )

        assert resp.status_code == 200
        assert resp.json()["data"]["card"]["title"] == "Decorators in Python"

    def test_review_nonexistent_draft(self, client):
        resp = client.post("/api/cards/nonexistent/review", json={"action": "accept"})

        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is False
        assert body["error"] == "Draft not found"


class TestDedup:
    def test_dedup_finds_existing_card(self, client):
        resp = client.post("/api/cards/extract", json={"content": "diff"})
        draft_id = resp.json()["data"]["drafts"][0]["id"]

        client.post(f"/api/cards/{draft_id}/review", json={"action": "accept"})

        resp2 = client.post("/api/cards/extract", json={"content": "diff"})
        draft_id2 = resp2.json()["data"]["drafts"][0]["id"]

        dedup_resp = client.get(f"/api/cards/{draft_id2}/dedup")
        assert dedup_resp.status_code == 200
        assert len(dedup_resp.json()["data"]["duplicates"]) >= 1

    def test_dedup_nonexistent_draft(self, client):
        resp = client.get("/api/cards/nonexistent/dedup")
        assert resp.json()["success"] is False


class TestListCards:
    def test_empty_list(self, client):
        resp = client.get("/api/cards/")
        assert resp.status_code == 200
        body = resp.json()
        assert body["data"]["cards"] == []
        assert body["data"]["total"] == 0

    def test_pagination(self, client):
        for _ in range(2):
            resp = client.post("/api/cards/extract", json={"content": "diff"})
            draft_id = resp.json()["data"]["drafts"][0]["id"]
            client.post(f"/api/cards/{draft_id}/review", json={"action": "accept"})

        resp = client.get("/api/cards/?page=1&limit=1")
        body = resp.json()
        assert len(body["data"]["cards"]) == 1
        assert body["data"]["total"] == 2
        assert body["data"]["page"] == 1
        assert body["data"]["limit"] == 1
