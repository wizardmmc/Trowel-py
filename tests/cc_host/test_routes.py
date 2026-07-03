"""Tests for cc_host.routes — HTTP + SSE endpoints.

A mini FastAPI app mounts only the cc router. The registry is overridden so
messages/interrupt/delete run against a FakeHost — never a real CC subprocess.
"""
import json
from pathlib import Path
from typing import AsyncIterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from trowel_py.cc_host import routes as cc_routes
from trowel_py.cc_host.routes import get_registry, router as cc_router
from trowel_py.schemas.cc_host import (
    SessionStartedEvent,
    TextEvent,
    FinishedEvent,
)


class FakeHost:
    """Duck-typed CCHost stand-in for route tests."""

    def __init__(self, events: list, cc_session_id: str = "s-1") -> None:
        self._events = events
        self.cc_session_id = cc_session_id
        self.model = "glm-5.2"
        self.effort = "medium"
        self.workdir = "/wd"
        self.interrupted = False
        self.closed = False
        self.received: list[str] = []

    async def send(self, text: str) -> AsyncIterator:
        self.received.append(text)
        for e in self._events:
            yield e

    async def interrupt(self) -> None:
        self.interrupted = True

    async def close(self) -> None:
        self.closed = True


def _mini_app(registry: dict) -> TestClient:
    app = FastAPI()
    app.include_router(cc_router, prefix="/api/cc")
    app.dependency_overrides[get_registry] = lambda: registry
    return TestClient(app)


def _parse_sse(resp) -> list[dict]:
    out = []
    for raw in resp.iter_lines():
        if isinstance(raw, bytes):
            raw = raw.decode()
        if raw.startswith("data: "):
            out.append(json.loads(raw[len("data: "):]))
    return out


class TestCreateSession:
    def test_creates_session_returns_envelope(self, tmp_path: Path):
        reg: dict = {}
        client = _mini_app(reg)
        resp = client.post("/api/cc/sessions", json={"workdir": str(tmp_path)})
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        sid = body["data"]["session_id"]
        assert sid in reg
        assert body["data"]["model"] == "glm-5.2"

    def test_create_with_resume(self, tmp_path: Path):
        reg: dict = {}
        client = _mini_app(reg)
        resp = client.post("/api/cc/sessions",
                           json={"workdir": str(tmp_path), "resume_from": "abc"})
        assert resp.json()["data"]["cc_session_id"] == "abc"

    def test_create_rejects_missing_workdir(self):
        client = _mini_app({})
        resp = client.post("/api/cc/sessions", json={"workdir": "/no/such/dir/here"})
        assert resp.status_code == 400


class TestMessagesSSE:
    def test_streams_trowel_events_as_sse(self):
        sid = "x"
        fake = FakeHost([
            SessionStartedEvent(type="session_started", model="glm-5.2",
                                cwd="/wd", cc_session_id="s-1", tools=["Read"]),
            TextEvent(type="text", text="hello"),
            FinishedEvent(type="finished", usage={}, total_cost_usd=0.0, num_turns=1),
        ])
        client = _mini_app({sid: fake})
        with client.stream("POST", f"/api/cc/sessions/{sid}/messages",
                           json={"text": "hi"}) as resp:
            assert resp.status_code == 200
            assert "text/event-stream" in resp.headers.get("content-type", "")
            events = _parse_sse(resp)
        kinds = [e["type"] for e in events]
        assert kinds == ["session_started", "text", "finished"]
        assert events[1]["text"] == "hello"

    def test_unknown_session_404(self):
        client = _mini_app({})
        resp = client.post("/api/cc/sessions/nope/messages", json={"text": "x"})
        assert resp.status_code == 404


class TestInterrupt:
    def test_interrupt_calls_host(self):
        sid = "x"
        fake = FakeHost([])
        client = _mini_app({sid: fake})
        resp = client.post(f"/api/cc/sessions/{sid}/interrupt")
        assert resp.status_code == 200
        assert resp.json()["data"]["interrupted"] is True
        assert fake.interrupted is True

    def test_interrupt_unknown_404(self):
        client = _mini_app({})
        assert client.post("/api/cc/sessions/nope/interrupt").status_code == 404


class TestDelete:
    def test_delete_closes_and_removes(self):
        sid = "x"
        fake = FakeHost([])
        reg = {sid: fake}
        client = _mini_app(reg)
        resp = client.delete(f"/api/cc/sessions/{sid}")
        assert resp.status_code == 200
        assert fake.closed is True
        assert sid not in reg


class TestListHistory:
    def test_list_returns_envelope_with_summaries(self, tmp_path: Path, monkeypatch):
        root = tmp_path / "projects"
        d = root / "-wd"
        d.mkdir(parents=True)
        (d / "sess-a.jsonl").write_text(
            json.dumps({"type": "user", "message": {"role": "user",
                "content": [{"type": "text", "text": "hi"}]}}) + "\n")
        monkeypatch.setattr("trowel_py.cc_host.session_scan.cc_projects_root", lambda: root)
        client = _mini_app({})
        resp = client.get("/api/cc/sessions", params={"workdir": "/wd"})
        assert resp.status_code == 200
        body = resp.json()
        data = body["data"]
        assert len(data) == 1
        assert data[0]["cc_session_id"] == "sess-a"
        assert data[0]["title"] == "hi"
        # meta.total reports the true on-disk count (for "共 N · 最近 M" display)
        assert body["meta"] == {"total": 1, "limit": 10}

    def test_list_caps_to_ten_most_recent(self, tmp_path: Path, monkeypatch):
        # the dropdown must not surface hundreds of sessions — verify the route
        # cap is wired (12 sessions → 10 newest, most-recent-first)
        import os
        root = tmp_path / "projects"
        d = root / "-wd"
        d.mkdir(parents=True)
        for i in range(12):
            f = d / f"s{i:02d}.jsonl"
            f.write_text(json.dumps({"type": "user", "message": {"role": "user",
                "content": [{"type": "text", "text": f"s{i:02d}"}]}}) + "\n")
            os.utime(f, (i, i))  # later index = newer
        monkeypatch.setattr("trowel_py.cc_host.session_scan.cc_projects_root", lambda: root)
        client = _mini_app({})
        resp = client.get("/api/cc/sessions", params={"workdir": "/wd"})
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert len(data) == 10
        # newest 10 are s02..s11 (mtime 2..11); s00 (mtime 0) and s01 dropped
        ids = [item["cc_session_id"] for item in data]
        assert ids[0] == "s11"  # most recent first
        assert "s00" not in ids and "s01" not in ids
        # meta.total is the true count (12 on disk) even though only 10 returned
        assert resp.json()["meta"] == {"total": 12, "limit": 10}


class TestGetHistory:
    def test_returns_normalized_events_for_session(self, tmp_path: Path, monkeypatch):
        """GET /sessions/{sid}/history replays the session jsonl as trowel events."""
        root = tmp_path / "projects"
        d = root / "-wd"
        d.mkdir(parents=True)
        (d / "sess-a.jsonl").write_text(
            json.dumps({"type": "user", "message": {"role": "user",
                "content": "hello"}}) + "\n"
            + json.dumps({"type": "assistant", "message": {"role": "assistant",
                "content": [{"type": "text", "text": "hi"}]}}) + "\n"
        )
        # parse_history imports cc_projects_root into its own namespace
        monkeypatch.setattr("trowel_py.cc_host.history.cc_projects_root", lambda: root)
        fake = FakeHost([], cc_session_id="sess-a")
        reg = {"s-1": fake}
        client = _mini_app(reg)
        resp = client.get("/api/cc/sessions/s-1/history")
        assert resp.status_code == 200
        events = resp.json()["data"]
        kinds = [e["type"] for e in events]
        assert "user" in kinds
        assert "text" in kinds

    def test_history_unknown_session_404(self):
        client = _mini_app({})
        resp = client.get("/api/cc/sessions/nope/history")
        assert resp.status_code == 404

    def test_history_no_cc_session_id_returns_empty(self):
        """A session with no cc_session_id yet returns an empty event list."""
        fake = FakeHost([], cc_session_id=None)
        client = _mini_app({"s-1": fake})
        resp = client.get("/api/cc/sessions/s-1/history")
        assert resp.status_code == 200
        assert resp.json()["data"] == []
