import sqlite3

import pytest
from fastapi.testclient import TestClient

from trowel_py.app import create_app
from trowel_py.cards.routes import _get_conn as cards_conn
from trowel_py.db.migrate import run_migrations
from trowel_py.review.routes import _get_conn as review_conn


@pytest.fixture
def review_client():
    app = create_app()

    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")

    run_migrations(conn)

    app.dependency_overrides[cards_conn] = lambda: conn
    app.dependency_overrides[review_conn] = lambda: conn

    yield TestClient(app), conn
    conn.close()
    app.dependency_overrides.clear()


def _seed_card_and_state(conn: sqlite3.Connection, card_id: str = "card-1") -> None:
    conn.execute(
        "INSERT INTO cards (id, title, category, explanation, tags, status) VALUES (?, ?, ?, ?, ?, ?)",
        (card_id, "Test Card", "test", "A test explanation", "[]", "active"),
    )
    conn.execute(
        "INSERT INTO fsrs_state (card_id, state, reps, due) VALUES (?, ?, ?, ?)",
        (card_id, 0, 0, "2020-01-01T00:00:00"),
    )


class TestDueEndpoint:
    def test_due_returns_due_cards(self, review_client):
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
        client, _ = review_client
        resp = client.get("/api/review/due")

        assert resp.status_code == 200
        assert resp.json()["data"] == []


class TestSubmitEndpoint:
    def test_good_rating_updates_fsrs_and_logs_review(self, review_client):
        client, conn = review_client
        _seed_card_and_state(conn, "card-1")

        resp = client.post(
            "/api/review/submit",
            json={
                "card_id": "card-1",
                "rating": 3,
            },
        )

        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert body["data"]["fsrs_state"]["reps"] == 1
        assert body["data"]["review_log"]["rating"] == 3

    def test_submit_unknown_card(self, review_client):
        client, _ = review_client

        resp = client.post(
            "/api/review/submit",
            json={
                "card_id": "ghost",
                "rating": 3,
            },
        )

        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is False
        assert body["error"] == "Card not found"

    def test_submit_invalid_rating_rejected(self, review_client):
        client, conn = review_client
        _seed_card_and_state(conn, "card-1")

        resp = client.post(
            "/api/review/submit",
            json={
                "card_id": "card-1",
                "rating": 0,
            },
        )
        assert resp.status_code == 422

        resp = client.post(
            "/api/review/submit",
            json={
                "card_id": "card-1",
                "rating": 5,
            },
        )
        assert resp.status_code == 422


class TestStatsEndpoints:
    def test_session_stats_empty(self, review_client):
        client, _ = review_client
        resp = client.get("/api/review/session-stats?since=2000-01-01T00:00:00")

        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert body["data"]["total"] == 0

    def test_overall_stats(self, review_client):
        client, _ = review_client
        resp = client.get("/api/review/stats")

        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert "total" in body["data"]
        assert "accuracy" in body["data"]


class TestFullReviewFlow:
    def test_card_not_due_after_review(self, review_client):
        client, conn = review_client
        _seed_card_and_state(conn, "card-1")

        due_resp = client.get("/api/review/due")
        assert len(due_resp.json()["data"]) == 1

        submit_resp = client.post(
            "/api/review/submit",
            json={
                "card_id": "card-1",
                "rating": 3,
            },
        )
        assert submit_resp.json()["success"] is True

        due_resp2 = client.get("/api/review/due")
        assert len(due_resp2.json()["data"]) == 0

    def test_good_review_advances_plant_and_reports_change(self, review_client):
        client, conn = review_client
        _seed_card_and_state(conn, "card-1")

        resp = client.post(
            "/api/review/submit",
            json={
                "card_id": "card-1",
                "rating": 3,
            },
        )
        plant = resp.json()["data"]["plant_stage"]

        assert plant in ("sprout", "tree")
        assert resp.json()["data"]["plant_changed"] is True
