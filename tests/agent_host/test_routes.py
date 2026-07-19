"""slice-072: /api/agent/* routes — HTTP + SSE wrapper around SessionHub.

Route tests inject a Hub wired to fakes via ``dependency_overrides[get_hub]``,
so nothing here touches the real store or any native subprocess (spec C-7).
The fakes are reused from test_hub.py (tests is a package, so cross-module
import works).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from trowel_py.agent_host.binding import Runtime, make_binding
from trowel_py.agent_host.hub import SessionHub
from trowel_py.agent_host.routes import get_hub, router
from trowel_py.agent_host.store import BindingStore
from tests.agent_host.test_hub import FakeCodexManager, make_cc_opener


@pytest.fixture
def hub(tmp_path: Path) -> SessionHub:
    store = BindingStore(tmp_path / "agent_sessions.json")
    cc_registry: dict[str, Any] = {}
    return SessionHub(
        store,
        codex_manager=FakeCodexManager(),
        cc_registry=cc_registry,
        cc_opener=make_cc_opener(cc_registry, {}),
    )


@pytest.fixture
def workdir(tmp_path: Path) -> Path:
    d = tmp_path / "proj"
    d.mkdir()
    return d


@pytest.fixture
def client(hub: SessionHub) -> TestClient:
    app = FastAPI()
    app.include_router(router, prefix="/api/agent")
    app.dependency_overrides[get_hub] = lambda: hub
    return TestClient(app)


def cc_payload(workdir: Path, **over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {"runtime": "claude_code", "workdir": str(workdir)}
    base.update(over)
    return base


def codex_payload(workdir: Path, **over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {"runtime": "codex", "workdir": str(workdir)}
    base.update(over)
    return base


# ---------------------------------------------------------------------------
# create
# ---------------------------------------------------------------------------


def test_post_sessions_creates_cc(client: TestClient, workdir: Path):
    resp = client.post("/api/agent/sessions", json=cc_payload(workdir))
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["runtime"] == "claude_code"
    assert data["session_id"]


def test_post_sessions_creates_codex(client: TestClient, workdir: Path):
    resp = client.post("/api/agent/sessions", json=codex_payload(workdir))
    assert resp.status_code == 200
    assert resp.json()["data"]["runtime"] == "codex"


def test_post_sessions_missing_workdir_400(client: TestClient):
    resp = client.post(
        "/api/agent/sessions", json=cc_payload(Path("/nonexistent/xyz-123"))
    )
    assert resp.status_code == 400


def test_post_sessions_invalid_runtime_422(client: TestClient, workdir: Path):
    resp = client.post(
        "/api/agent/sessions",
        json={"runtime": "gemini", "workdir": str(workdir)},
    )
    assert resp.status_code == 422  # Literal validation


# ---------------------------------------------------------------------------
# active / activate / get / delete
# ---------------------------------------------------------------------------


def test_get_active_lists_mixed(client: TestClient, workdir: Path):
    client.post("/api/agent/sessions", json=cc_payload(workdir))
    client.post("/api/agent/sessions", json=codex_payload(workdir))
    sessions = client.get("/api/agent/sessions/active").json()["data"]["sessions"]
    assert {s["runtime"] for s in sessions} == {"claude_code", "codex"}


def test_post_activate_sets_active(client: TestClient, workdir: Path):
    r1 = client.post("/api/agent/sessions", json=cc_payload(workdir)).json()["data"]
    r2 = client.post("/api/agent/sessions", json=codex_payload(workdir)).json()["data"]
    resp = client.post(f"/api/agent/sessions/{r1['session_id']}/activate")
    assert resp.json()["data"]["active_id"] == r1["session_id"]
    active = client.get("/api/agent/sessions/active").json()["data"]["active_id"]
    assert active == r1["session_id"]
    # r2 still in the list (切换不销毁)
    ids = {
        s["session_id"]
        for s in client.get("/api/agent/sessions/active").json()["data"]["sessions"]
    }
    assert r2["session_id"] in ids


def test_get_session(client: TestClient, workdir: Path):
    r = client.post("/api/agent/sessions", json=cc_payload(workdir)).json()["data"]
    got = client.get(f"/api/agent/sessions/{r['session_id']}").json()["data"]
    assert got["session_id"] == r["session_id"]


def test_get_session_404(client: TestClient):
    assert client.get("/api/agent/sessions/nope").status_code == 404


def test_delete_session(client: TestClient, workdir: Path):
    r = client.post("/api/agent/sessions", json=cc_payload(workdir)).json()["data"]
    resp = client.delete(f"/api/agent/sessions/{r['session_id']}")
    assert resp.json()["data"]["closed"] is True
    assert client.get(f"/api/agent/sessions/{r['session_id']}").status_code == 404


# ---------------------------------------------------------------------------
# patch runtime frozen (C-1) + cross-resume (C-2)
# ---------------------------------------------------------------------------


def test_patch_runtime_change_rejected_422(client: TestClient, workdir: Path):
    r = client.post("/api/agent/sessions", json=cc_payload(workdir)).json()["data"]
    resp = client.patch(
        f"/api/agent/sessions/{r['session_id']}", json={"runtime": "codex"}
    )
    assert resp.status_code == 422


def test_cross_resume_rejected_409(
    client: TestClient, workdir: Path, hub: SessionHub
):
    """Resume a native id already bound to CC, as Codex → 409 (C-2)."""

    hub.store.put(
        make_binding(
            session_id="old-cc",
            runtime=Runtime.CLAUDE_CODE,
            native_session_id="cc-jsonl-1",
            workdir=str(workdir),
            model=None,
            effort=None,
            permission=None,
            memory_enabled=True,
            profile_enabled=True,
            capabilities=("tools",),
            name="proj",
        )
    )
    resp = client.post(
        "/api/agent/sessions",
        json=codex_payload(workdir, resume_from="cc-jsonl-1"),
    )
    assert resp.status_code == 409


# ---------------------------------------------------------------------------
# runtimes + history
# ---------------------------------------------------------------------------


def test_get_runtimes(client: TestClient):
    runtimes = client.get("/api/agent/runtimes").json()["data"]
    assert {r["runtime"] for r in runtimes} == {"claude_code", "codex"}
    assert all("capabilities" in r for r in runtimes)
    codex = next(r for r in runtimes if r["runtime"] == "codex")
    assert codex["connected"] is True  # FakeCodexManager wired


def test_get_history_returns_codex_rows_for_workdir(
    client: TestClient, workdir: Path
):
    client.post("/api/agent/sessions", json=codex_payload(workdir))
    rows = client.get(
        f"/api/agent/sessions?workdir={workdir}"
    ).json()["data"]
    codex_rows = [r for r in rows if r["runtime"] == "codex"]
    assert codex_rows
    assert codex_rows[0]["native_session_id"] is None  # fresh, no thread yet


# ---------------------------------------------------------------------------
# interrupt + messages (SSE)
# ---------------------------------------------------------------------------


def test_post_interrupt(client: TestClient, workdir: Path):
    r = client.post("/api/agent/sessions", json=cc_payload(workdir)).json()["data"]
    resp = client.post(f"/api/agent/sessions/{r['session_id']}/interrupt")
    assert resp.status_code == 200
    assert resp.json()["data"]["interrupted"] is True


def test_post_messages_streams_sse(client: TestClient, workdir: Path):
    r = client.post("/api/agent/sessions", json=cc_payload(workdir)).json()["data"]
    with client.stream(
        "POST",
        f"/api/agent/sessions/{r['session_id']}/messages",
        json={"text": "hi"},
    ) as resp:
        assert resp.status_code == 200
        body = b"".join(resp.iter_bytes())
    assert b"data:" in body
    assert b"claude_code" in body  # runtime tag on every frame


def test_post_messages_unknown_session_emits_error_frame(client: TestClient):
    with client.stream(
        "POST", "/api/agent/sessions/nope/messages", json={"text": "hi"}
    ) as resp:
        body = b"".join(resp.iter_bytes())
    assert b"data:" in body
    assert b"error" in body
