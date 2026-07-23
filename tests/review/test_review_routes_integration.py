"""Integration tests for review routes — HTTP level with full review→submit→verify flow."""
import sqlite3

import pytest
from fastapi.testclient import TestClient

from trowel_py.app import create_app
from trowel_py.cards.routes import _get_conn as card_get_conn


@pytest.fixture
def review_client():
    """Create app with in-memory DB, set up cards + fsrs_state tables, override dependencies."""
    app = create_app()

    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")

    # Run migrations to create all tables
    from trowel_py.db.migrate import run_migrations
    run_migrations(conn)

    # Override DB connection for both card and review routes
    from trowel_py.cards.routes import _get_conn as cards_conn
    from trowel_py.review.routes import _get_conn as review_conn
    app.dependency_overrides[cards_conn] = lambda: conn
    app.dependency_overrides[review_conn] = lambda: conn

    yield TestClient(app), conn
    conn.close()
    app.dependency_overrides.clear()


def _seed_card_and_state(conn: sqlite3.Connection, card_id: str = "card-1") -> None:
    """Helper: insert a card + fsrs_state so it's due for review."""
    conn.execute(
        "INSERT INTO cards (id, title, category, explanation, tags, status) VALUES (?, ?, ?, ?, ?, ?)",
        (card_id, "Test Card", "test", "A test explanation", "[]", "active"),
    )
    conn.execute(
        "INSERT INTO fsrs_state (card_id, state, reps, due) VALUES (?, ?, ?, ?)",
        (card_id, 0, 0, "2020-01-01T00:00:00"),  # past due, never reviewed
    )


class TestDueEndpoint:
    """Tests for GET /api/review/due."""

    def test_due_returns_due_cards(self, review_client):
        """Cards with past due date should appear in due list."""
        client, conn = review_client
        _seed_card_and_state(conn, "card-1")

        resp = client.get("/api/review/due")

        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert len(body["data"]) == 1
        assert body["data"][0]["card"]["title"] == "Test Card"
        assert body["data"][0]["plant_stage"] == "seed"

    def test_due_empty_when_no_cards(self, review_client):
        """No due cards should return empty data."""
        client, _ = review_client
        resp = client.get("/api/review/due")

        assert resp.status_code == 200
        assert resp.json()["data"] == []


class TestSubmitEndpoint:
    """Tests for POST /api/review/submit."""

    def test_submit_rating_good(self, review_client):
        """Submitting Good rating should update FSRS state."""
        client, conn = review_client
        _seed_card_and_state(conn, "card-1")

        resp = client.post("/api/review/submit", json={
            "card_id": "card-1", "rating": 3,
        })

        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert body["data"]["fsrs_state"]["reps"] == 1
        assert body["data"]["review_log"]["rating"] == 3

    def test_submit_unknown_card(self, review_client):
        """Submitting for non-existent card should return error."""
        client, _ = review_client

        resp = client.post("/api/review/submit", json={
            "card_id": "ghost", "rating": 3,
        })

        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is False
        assert body["error"] == "Card not found"

    def test_submit_invalid_rating_rejected(self, review_client):
        """Rating outside 1-4 should return 422 validation error."""
        client, conn = review_client
        _seed_card_and_state(conn, "card-1")

        # Rating = 0 (below minimum)
        resp = client.post("/api/review/submit", json={
            "card_id": "card-1", "rating": 0,
        })
        assert resp.status_code == 422

        # Rating = 5 (above maximum)
        resp = client.post("/api/review/submit", json={
            "card_id": "card-1", "rating": 5,
        })
        assert resp.status_code == 422


class TestStatsEndpoints:
    """Tests for GET /api/review/session-stats and /stats."""

    def test_session_stats_empty(self, review_client):
        """No reviews should return zeroed stats."""
        client, _ = review_client
        resp = client.get("/api/review/session-stats?since=2000-01-01T00:00:00")

        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert body["data"]["total"] == 0

    def test_overall_stats(self, review_client):
        """GET /stats should return aggregated stats."""
        client, _ = review_client
        resp = client.get("/api/review/stats")

        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert "total" in body["data"]
        assert "accuracy" in body["data"]


class TestFullReviewFlow:
    """End-to-end: accept card → due → submit → not due anymore."""

    def test_card_not_due_after_review(self, review_client):
        """After submitting a review, card should no longer be in due list."""
        client, conn = review_client
        _seed_card_and_state(conn, "card-1")

        # Card should be due
        due_resp = client.get("/api/review/due")
        assert len(due_resp.json()["data"]) == 1

        # Submit review
        submit_resp = client.post("/api/review/submit", json={
            "card_id": "card-1", "rating": 3,
        })
        assert submit_resp.json()["success"] is True

        # Card should no longer be due
        due_resp2 = client.get("/api/review/due")
        assert len(due_resp2.json()["data"]) == 0

    def test_plant_starts_as_seed_or_sprout(self, review_client):
        """After first review, plant should be at least sprout (not seed)."""
        client, conn = review_client
        _seed_card_and_state(conn, "card-1")

        resp = client.post("/api/review/submit", json={
            "card_id": "card-1", "rating": 3,
        })
        plant = resp.json()["data"]["plant_stage"]

        # First review always transitions from seed to at least sprout
        assert plant in ("sprout", "tree")
        assert resp.json()["data"]["plant_changed"] is True
