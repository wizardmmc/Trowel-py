"""Tests for cc_host.routes — HTTP + SSE endpoints.

A mini FastAPI app mounts only the cc router. The registry is overridden so
messages/interrupt/delete run against a FakeHost — never a real CC subprocess.
"""
import json
import subprocess
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

    def __init__(self, events: list, cc_session_id: str = "s-1", workdir: str = "/wd") -> None:
        self._events = events
        self.cc_session_id = cc_session_id
        self.model = "glm-5.2"
        self.effort = "medium"
        self.workdir = workdir
        self.interrupted = False
        self.closed = False
        self.reloaded = False
        self.received: list[str] = []
        self.answered: dict[str, str] | None = None
        self.cancelled = False

    async def send(self, text: str) -> AsyncIterator:
        self.received.append(text)
        for e in self._events:
            yield e

    async def interrupt(self) -> None:
        self.interrupted = True

    async def answer_elicit(self, answers: dict[str, str]) -> bool:
        self.answered = dict(answers)
        return True

    async def cancel_elicit(self) -> bool:
        self.cancelled = True
        return True

    async def reload(self) -> None:
        self.reloaded = True

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


class TestAnswer:
    def test_answer_calls_host_with_answers(self):
        fake = FakeHost([])
        client = _mini_app({"s1": fake})
        resp = client.post("/api/cc/sessions/s1/answer",
                           json={"answers": {"A or B?": "A"}, "cancel": False})
        body = resp.json()
        assert body["success"] is True
        assert body["data"]["answered"] is True
        assert fake.answered == {"A or B?": "A"}

    def test_answer_cancel_calls_host(self):
        fake = FakeHost([])
        client = _mini_app({"s1": fake})
        resp = client.post("/api/cc/sessions/s1/answer",
                           json={"answers": {}, "cancel": True})
        assert resp.json()["success"] is True
        assert fake.cancelled is True
        assert fake.answered is None

    def test_answer_unknown_404(self):
        client = _mini_app({})
        assert client.post("/api/cc/sessions/nope/answer",
                           json={"answers": {}, "cancel": False}).status_code == 404


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
        import uuid as uuidlib

        root = tmp_path / "projects"
        d = root / "-wd"
        d.mkdir(parents=True)
        # cc writes sessions as <uuid>.jsonl with a real first user message;
        # the filter (slice-026 C3) requires a UUID stem + a real prompt.
        sid = str(uuidlib.uuid4())
        (d / f"{sid}.jsonl").write_text(
            json.dumps({"type": "user", "isSidechain": False, "message": {"role": "user",
                "content": [{"type": "text", "text": "hi"}]}}) + "\n")
        monkeypatch.setattr("trowel_py.cc_host.session_scan.cc_projects_root", lambda: root)
        client = _mini_app({})
        resp = client.get("/api/cc/sessions", params={"workdir": "/wd"})
        assert resp.status_code == 200
        body = resp.json()
        data = body["data"]
        assert len(data) == 1
        assert data[0]["cc_session_id"] == sid
        assert data[0]["title"] == "hi"
        # meta.total reports the filtered count (cc --resume's view)
        assert body["meta"] == {"total": 1, "limit": 10}

    def test_list_caps_to_ten_most_recent(self, tmp_path: Path, monkeypatch):
        # the dropdown must not surface hundreds of sessions — verify the route
        # cap is wired (12 sessions → 10 newest, most-recent-first). Filenames
        # are UUID stems (the filter rejects non-UUID names).
        import os
        import uuid as uuidlib

        root = tmp_path / "projects"
        d = root / "-wd"
        d.mkdir(parents=True)
        stems = []
        for i in range(12):
            stem = str(uuidlib.uuid4())
            stems.append(stem)
            f = d / f"{stem}.jsonl"
            f.write_text(json.dumps({"type": "user", "isSidechain": False, "message": {"role": "user",
                "content": [{"type": "text", "text": f"s{i:02d}"}]}}) + "\n")
            os.utime(f, (i, i))  # later index = newer
        monkeypatch.setattr("trowel_py.cc_host.session_scan.cc_projects_root", lambda: root)
        client = _mini_app({})
        resp = client.get("/api/cc/sessions", params={"workdir": "/wd"})
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert len(data) == 10
        # newest 10 by mtime (indices 2..11); two oldest dropped. UUID stems,
        # most-recent-first.
        ids = [item["cc_session_id"] for item in data]
        assert ids[0] == stems[11]
        assert stems[0] not in ids and stems[1] not in ids
        # meta.total is the filtered count (12 valid here) even though 10 returned
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


# ---------------------------------------------------------------------------
# slice-026 E1: revert endpoint + revert_enabled
# ---------------------------------------------------------------------------


def _git_repo(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=path, check=True)
    (path / "seed").write_text("x\n")
    subprocess.run(["git", "add", "-A"], cwd=path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "seed"], cwd=path, check=True)
    return path


class TestRevert:
    def test_create_session_revert_enabled_true_for_git(self, tmp_path: Path):
        _git_repo(tmp_path / "repo")
        client = _mini_app({})
        resp = client.post("/api/cc/sessions", json={"workdir": str(tmp_path / "repo")})
        assert resp.status_code == 200
        assert resp.json()["data"]["revert_enabled"] is True

    def test_create_session_revert_enabled_false_for_non_git(self, tmp_path: Path):
        norepo = tmp_path / "norepo"
        norepo.mkdir()
        client = _mini_app({})
        resp = client.post("/api/cc/sessions", json={"workdir": str(norepo)})
        assert resp.json()["data"]["revert_enabled"] is False

    def test_revert_unknown_turn_404(self, tmp_path: Path):
        repo = _git_repo(tmp_path / "repo")
        sid = "s1"
        host = FakeHost(events=[], workdir=str(repo))
        client = _mini_app({sid: host})
        resp = client.post(f"/api/cc/sessions/{sid}/revert", json={"turn_id": "nope"})
        assert resp.status_code == 404
        assert host.reloaded is False  # not called on the failure path

    def test_revert_non_git_400(self, tmp_path: Path):
        norepo = tmp_path / "norepo"
        norepo.mkdir()
        sid = "s1"
        host = FakeHost(events=[], workdir=str(norepo))
        client = _mini_app({sid: host})
        resp = client.post(f"/api/cc/sessions/{sid}/revert", json={"turn_id": "x"})
        assert resp.status_code == 400

    def test_revert_restores_files_and_reloads_process(self, tmp_path: Path):
        import uuid as uuidlib

        repo = _git_repo(tmp_path / "repo")
        # pre-save a checkpoint via the module, then mutate a tracked file
        turn_id = uuidlib.uuid4().hex
        ckpt_dir = tmp_path / "ckpt_src"
        ckpt_dir.mkdir()
        from trowel_py.cc_host import checkpoint as ckpt

        ckpt.save(str(repo), turn_id)
        (repo / "seed").write_text("changed during the turn\n")
        assert repo.joinpath("seed").read_text() != "x\n"

        sid = "s1"
        host = FakeHost(events=[], workdir=str(repo))
        client = _mini_app({sid: host})
        resp = client.post(f"/api/cc/sessions/{sid}/revert", json={"turn_id": turn_id})
        assert resp.status_code == 200
        # file restored to the snapshot
        assert (repo / "seed").read_text() == "x\n"
        # process was invalidated so the next send re-resumes
        assert host.reloaded is True
        assert resp.json()["data"]["reverted_turn_id"] == turn_id
