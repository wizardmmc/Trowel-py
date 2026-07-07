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
        # 模拟 CCHost.is_dead（True=无活进程/temp，False=live）。默认 False：
        # FakeHost 通常代表已 send 过的 live session；temp 场景测试可显式置 True。
        self.is_dead = False

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
    # slice-028 D2: reset 多开模块级状态（避免测试间污染）
    cc_routes._WORKDIR_INDEX.clear()
    cc_routes._SESSION_NAMES.clear()
    cc_routes._ACTIVE_SID = None
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
        assert body["data"]["model"] is None  # slice-027: default follows cc settings.json

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


class TestMultiSession:
    """slice-028 D2: 多 session 并存 + 命名序号 + active/activate + 资源限制。"""

    def test_same_workdir_sessions_get_suffix(self, tmp_path: Path):
        """Q6: 同 workdir 多开，第一个 basename，第二个 basename #2。"""
        client = _mini_app({})
        client.post("/api/cc/sessions", json={"workdir": str(tmp_path)})
        client.post("/api/cc/sessions", json={"workdir": str(tmp_path)})
        data = client.get("/api/cc/sessions/active").json()["data"]
        names = sorted(s["name"] for s in data["sessions"])
        assert tmp_path.name in names
        assert f"{tmp_path.name} #2" in names

    def test_active_returns_sessions_and_active_id(self, tmp_path: Path):
        """GET /sessions/active 返回活跃列表 + active_id；新建自动设为活跃。"""
        client = _mini_app({})
        sid = client.post("/api/cc/sessions", json={"workdir": str(tmp_path)}).json()["data"]["session_id"]
        data = client.get("/api/cc/sessions/active").json()["data"]
        assert len(data["sessions"]) == 1
        assert data["sessions"][0]["id"] == sid
        assert data["sessions"][0]["workdir"] == str(tmp_path)
        assert data["active_id"] == sid  # 新建自动活跃

    def test_active_lists_connected_flag(self, tmp_path: Path):
        """GET /sessions/active 每条带 connected 字段：temp（is_dead=True）→ False，
        live（is_dead=False）→ True。前端 reconcile 据此判断是否进多开栏，避免
        没发过消息的 temp 被误显示（bug：多出 ClaudeDesktop 多开）。"""
        reg: dict = {}
        client = _mini_app(reg)
        # temp：真实 CCHost 创建后 _proc=None → is_dead=True
        temp_sid = client.post(
            "/api/cc/sessions", json={"workdir": str(tmp_path)}
        ).json()["data"]["session_id"]
        # live：直接塞一个 is_dead=False 的 FakeHost
        live_host = FakeHost(events=[], workdir=str(tmp_path))
        live_sid = "live-fake-1"
        reg[live_sid] = live_host
        cc_routes._WORKDIR_INDEX.setdefault(str(tmp_path), set()).add(live_sid)
        cc_routes._SESSION_NAMES[live_sid] = tmp_path.name

        data = client.get("/api/cc/sessions/active").json()["data"]
        by_id = {s["id"]: s for s in data["sessions"]}
        assert by_id[temp_sid]["connected"] is False  # temp
        assert by_id[live_sid]["connected"] is True   # live

    def test_activate_switches_active(self, tmp_path: Path):
        """POST /sessions/:id/activate 切当前活跃。"""
        client = _mini_app({})
        sid1 = client.post("/api/cc/sessions", json={"workdir": str(tmp_path)}).json()["data"]["session_id"]
        sid2 = client.post("/api/cc/sessions", json={"workdir": str(tmp_path)}).json()["data"]["session_id"]
        resp = client.post(f"/api/cc/sessions/{sid1}/activate")
        assert resp.json()["data"]["active_id"] == sid1
        assert client.get("/api/cc/sessions/active").json()["data"]["active_id"] == sid1

    def test_activate_unknown_session_404(self, tmp_path: Path):
        client = _mini_app({})
        resp = client.post("/api/cc/sessions/nope/activate")
        assert resp.status_code == 404

    def test_create_rejects_over_connection_limit(self, tmp_path: Path, monkeypatch):
        """Q5': 连接数 > MAX_CONNECTIONS 拒绝（409）。"""
        monkeypatch.setattr(cc_routes, "MAX_CONNECTIONS", 2)
        client = _mini_app({})
        client.post("/api/cc/sessions", json={"workdir": str(tmp_path)})
        client.post("/api/cc/sessions", json={"workdir": str(tmp_path)})
        resp = client.post("/api/cc/sessions", json={"workdir": str(tmp_path)})
        assert resp.status_code == 409

    def test_delete_clears_multi_state(self, tmp_path: Path):
        """DELETE session 清理 _WORKDIR_INDEX / _SESSION_NAMES / _ACTIVE_SID。"""
        client = _mini_app({})
        sid = client.post("/api/cc/sessions", json={"workdir": str(tmp_path)}).json()["data"]["session_id"]
        resp = client.delete(f"/api/cc/sessions/{sid}")
        assert resp.json()["success"]
        data = client.get("/api/cc/sessions/active").json()["data"]
        assert data["sessions"] == []
        assert data["active_id"] is None
        assert cc_routes._WORKDIR_INDEX.get(str(tmp_path)) is None


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


class TestModelsEndpoint:
    """slice-027 C2: GET /api/cc/models — list cc aliases from settings.json env."""

    def test_returns_alias_envelope(self, monkeypatch):
        from trowel_py.cc_host.models import ModelOption

        monkeypatch.setattr(
            "trowel_py.cc_host.routes.list_models",
            lambda: [ModelOption(value="opus", label="Opus",
                                 real_model="glm-5.2[1M]",
                                 description="最强推理")],
        )
        client = _mini_app({})
        resp = client.get("/api/cc/models")
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert body["error"] is None
        assert body["data"][0]["value"] == "opus"
        assert body["data"][0]["label"] == "Opus"
        assert body["data"][0]["real_model"] == "glm-5.2[1M]"

    def test_empty_envelope_when_no_settings(self, monkeypatch):
        monkeypatch.setattr("trowel_py.cc_host.routes.list_models", lambda: [])
        client = _mini_app({})
        resp = client.get("/api/cc/models")
        assert resp.status_code == 200
        assert resp.json()["data"] == []


class TestSlashItemsEndpoint:
    """slice-027 C1: GET /api/cc/slash-items — '/' autocomplete data source."""

    def test_returns_items_envelope(self, monkeypatch):
        from trowel_py.cc_host.slash_items import SlashItem

        monkeypatch.setattr(
            "trowel_py.cc_host.routes.list_slash_items",
            lambda workdir: [SlashItem(name="monthly-etf", description="月度ETF",
                                       source="user", type="skill")],
        )
        client = _mini_app({})
        resp = client.get("/api/cc/slash-items?workdir=/wd")
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert body["error"] is None
        assert body["data"][0]["name"] == "monthly-etf"
        assert body["data"][0]["type"] == "skill"

    def test_requires_workdir_query(self, monkeypatch):
        client = _mini_app({})
        resp = client.get("/api/cc/slash-items")
        assert resp.status_code == 422  # missing required query param


class TestListDirEndpoint:
    """slice-027 C4: GET /api/cc/list-dir?path=... — feeds WorkdirPicker's tree
    (browser sandbox can't enumerate local dirs)."""

    def test_lists_immediate_subdirectories_sorted(self, tmp_path: Path):
        (tmp_path / "b").mkdir()
        (tmp_path / "a").mkdir()
        (tmp_path / "file.txt").write_text("x")  # files omitted
        (tmp_path / ".hidden").mkdir()  # dotted omitted
        client = _mini_app({})
        resp = client.get(f"/api/cc/list-dir?path={tmp_path}")
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert [c["name"] for c in body["data"]] == ["a", "b"]

    def test_child_carries_absolute_path(self, tmp_path: Path):
        (tmp_path / "sub").mkdir()
        client = _mini_app({})
        resp = client.get(f"/api/cc/list-dir?path={tmp_path}")
        child = resp.json()["data"][0]
        assert child["name"] == "sub"
        assert child["path"] == str(tmp_path / "sub")

    def test_nonexistent_path_returns_400(self, tmp_path: Path):
        client = _mini_app({})
        resp = client.get(f"/api/cc/list-dir?path={tmp_path / 'nope'}")
        assert resp.status_code == 400

    def test_file_path_returns_400(self, tmp_path: Path):
        f = tmp_path / "f.txt"
        f.write_text("x")
        client = _mini_app({})
        resp = client.get(f"/api/cc/list-dir?path={f}")
        assert resp.status_code == 400

    def test_expands_tilde_to_home(self, tmp_path: Path, monkeypatch):
        """`~` should resolve to the user's home so the user can type it."""
        monkeypatch.setenv("HOME", str(tmp_path))
        (tmp_path / "realdir").mkdir()
        client = _mini_app({})
        resp = client.get("/api/cc/list-dir?path=~")
        assert resp.status_code == 200
        assert [c["name"] for c in resp.json()["data"]] == ["realdir"]
