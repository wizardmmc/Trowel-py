"""Integration tests for card routes — HTTP level."""
import sqlite3
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from trowel_py.app import create_app
from trowel_py.cards.routes import _get_conn, _get_llm_service
from trowel_py.llm.client import LLMService
from trowel_py.schemas.extracted_card import ExtractedCard, ExtractOutput


@pytest.fixture
def app_with_db():
    """Create app with in-memory DB, overriding _get_conn and _get_llm_service."""
    app = create_app()

    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("""
        CREATE TABLE cards(
            id TEXT PRIMARY KEY, title TEXT NOT NULL, category TEXT NOT NULL,
            explanation TEXT NOT NULL, example TEXT, difficulty INTEGER DEFAULT 3,
            source TEXT, tags TEXT,
            status TEXT DEFAULT 'active' CHECK(status IN ('active','archived','draft')),
            created_at TEXT DEFAULT (datetime('now')), updated_at TEXT DEFAULT (datetime('now'))
        )
    """)
    conn.execute("""
        CREATE VIRTUAL TABLE cards_fts USING fts5(
            title, explanation, tags, content=cards, content_rowid=rowid
        )
    """)
    conn.execute("""
        CREATE TRIGGER cards_fts_ai AFTER INSERT ON cards BEGIN
            INSERT INTO cards_fts(rowid, title, explanation, tags)
            VALUES (new.rowid, new.title, new.explanation, new.tags);
        END
    """)
    conn.execute("""
        CREATE TABLE fsrs_state(
            card_id TEXT PRIMARY KEY REFERENCES cards(id) ON DELETE CASCADE,
            stability REAL DEFAULT 0, difficulty REAL DEFAULT 0,
            elapsed_days INTEGER DEFAULT 0, scheduled_days INTEGER DEFAULT 0,
            reps INTEGER DEFAULT 0, lapses INTEGER DEFAULT 0,
            state INTEGER DEFAULT 0, due TEXT DEFAULT (datetime('now')),
            last_review TEXT
        )
    """)

    app.dependency_overrides[_get_conn] = lambda: conn
    app.dependency_overrides[_get_llm_service] = lambda: _mock_llm_service()
    yield app, conn
    conn.close()
    app.dependency_overrides.clear()


@pytest.fixture
def client(app_with_db):
    """TestClient wired to app with overridden dependencies."""
    app, _ = app_with_db
    return TestClient(app)


def _mock_llm_service():
    """Mock LLM that always returns one fixed card."""
    mock = MagicMock(spec=LLMService)
    mock.structured_call.return_value = ExtractOutput(cards=[
        ExtractedCard(
            title="Python Decorators",
            category="Python",
            explanation="A decorator wraps a function to extend its behavior without modifying it",
            tags=["python"],
            confidence=4,
            source_type="git_diff",
        ),
    ])
    return mock


# --- Extract ---

class TestExtract:
    def test_extract_returns_drafts(self, client):
        """POST /extract with valid content should return draft cards."""
        resp = client.post("/api/cards/extract", json={"content": "some diff text"})

        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert len(body["data"]["drafts"]) == 1
        assert body["data"]["drafts"][0]["title"] == "Python Decorators"

    def test_extract_empty_content_rejected(self, client):
        """POST /extract with empty content should return 422 validation error."""
        resp = client.post("/api/cards/extract", json={"content": ""})
        assert resp.status_code == 422


# --- Review (accept / reject / edit) ---

class TestReview:
    def _extract_draft(self, client, app_with_db):
        """Helper: extract a draft, return its id."""
        resp = client.post("/api/cards/extract", json={"content": "some diff"})
        return resp.json()["data"]["drafts"][0]["id"]

    def test_accept_creates_card(self, client, app_with_db):
        """Accept should create card in DB + FSRS state."""
        app, conn = app_with_db
        draft_id = self._extract_draft(client, app_with_db)

        resp = client.post(f"/api/cards/{draft_id}/review", json={"action": "accept"})

        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert body["data"]["card"]["title"] == "Python Decorators"
        assert body["data"]["card"]["status"] == "active"

        # verify DB
        cards = conn.execute("SELECT * FROM cards").fetchall()
        assert len(cards) == 1
        fsrs = conn.execute("SELECT * FROM fsrs_state").fetchall()
        assert len(fsrs) == 1

    def test_reject_no_card_created(self, client, app_with_db):
        """Reject should not create any card or FSRS state."""
        app, conn = app_with_db
        draft_id = self._extract_draft(client, app_with_db)

        resp = client.post(f"/api/cards/{draft_id}/review", json={"action": "reject"})

        assert resp.status_code == 200
        body = resp.json()
        assert body["data"]["rejected"] is True

        cards = conn.execute("SELECT * FROM cards").fetchall()
        assert len(cards) == 0
        fsrs = conn.execute("SELECT * FROM fsrs_state").fetchall()
        assert len(fsrs) == 0

    def test_edit_with_changes(self, client, app_with_db):
        """Edit should apply user edits then create card."""
        app, conn = app_with_db
        draft_id = self._extract_draft(client, app_with_db)

        resp = client.post(f"/api/cards/{draft_id}/review", json={
            "action": "edit",
            "edits": {"title": "Decorators in Python"},
        })

        assert resp.status_code == 200
        assert resp.json()["data"]["card"]["title"] == "Decorators in Python"

    def test_review_nonexistent_draft(self, client):
        """Reviewing a nonexistent draft_id should return error."""
        resp = client.post("/api/cards/nonexistent/review", json={"action": "accept"})

        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is False
        assert body["error"] == "Draft not found"


# --- Dedup ---

class TestDedup:
    def test_dedup_finds_existing_card(self, client, app_with_db):
        """After accepting a card, extracting same title should find duplicate."""
        app, conn = app_with_db

        # extract a draft
        resp = client.post("/api/cards/extract", json={"content": "diff"})
        draft_id = resp.json()["data"]["drafts"][0]["id"]

        # accept it first (so it's in the DB)
        client.post(f"/api/cards/{draft_id}/review", json={"action": "accept"})

        # extract again — same title, should find duplicate
        resp2 = client.post("/api/cards/extract", json={"content": "diff"})
        draft_id2 = resp2.json()["data"]["drafts"][0]["id"]

        dedup_resp = client.get(f"/api/cards/{draft_id2}/dedup")
        assert dedup_resp.status_code == 200
        assert len(dedup_resp.json()["data"]["duplicates"]) >= 1

    def test_dedup_nonexistent_draft(self, client):
        """Dedup for nonexistent draft_id should return error."""
        resp = client.get("/api/cards/nonexistent/dedup")
        assert resp.json()["success"] is False


# --- List cards ---

class TestListCards:
    def test_empty_list(self, client):
        """GET / with no cards should return empty list with total=0."""
        resp = client.get("/api/cards/")
        assert resp.status_code == 200
        body = resp.json()
        assert body["data"]["cards"] == []
        assert body["data"]["total"] == 0

    def test_pagination(self, client, app_with_db):
        """GET / with page=1&limit=1 should return 1 card, total=2."""
        app, conn = app_with_db

        # create 2 cards via extract + accept
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
