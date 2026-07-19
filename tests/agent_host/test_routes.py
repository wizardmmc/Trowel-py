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


def test_patch_codex_model_effort_returns_adjusted_pair(
    client: TestClient, workdir: Path
):
    """PATCH exposes the server-side Luna fallback instead of hiding it."""

    row = client.post(
        "/api/agent/sessions",
        json=codex_payload(workdir, model="gpt-5.6-sol", effort="ultra"),
    ).json()["data"]
    response = client.patch(
        f"/api/agent/sessions/{row['session_id']}",
        json={"model": "gpt-5.6-luna", "effort": "ultra"},
    )
    assert response.status_code == 200
    assert response.json()["data"] == {
        "model": "gpt-5.6-luna",
        "effort": "medium",
        "adjusted": True,
    }


def test_cross_resume_rejected_409(client: TestClient, workdir: Path, hub: SessionHub):
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


def test_get_models_returns_the_manager_catalog(
    client: TestClient, hub: SessionHub, monkeypatch: pytest.MonkeyPatch
):
    """The API returns exactly the native rows; it owns no model whitelist."""

    native = [
        {
            "id": "future-model",
            "model": "future-model-native",
            "display_name": "Future",
            "description": "Recorded by the fake app-server.",
            "is_default": True,
            "default_effort": "quantum",
            "supported_efforts": [
                {"value": "quantum", "description": "Unknown future effort"}
            ],
        }
    ]

    async def list_models():
        return native

    monkeypatch.setattr(hub._codex, "list_models", list_models)  # noqa: SLF001
    response = client.get("/api/agent/models")
    assert response.status_code == 200
    assert response.json()["data"]["models"] == native


def test_get_history_returns_codex_rows_for_workdir(client: TestClient, workdir: Path):
    client.post("/api/agent/sessions", json=codex_payload(workdir))
    rows = client.get(f"/api/agent/sessions?workdir={workdir}").json()["data"]
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


def test_post_answer_codex_request_routes_by_session(
    client: TestClient, workdir: Path, hub: SessionHub
):
    """slice-075 answer endpoint keeps session + request identity together."""

    row = client.post(
        "/api/agent/sessions", json=codex_payload(workdir)
    ).json()["data"]
    response = client.post(
        f"/api/agent/sessions/{row['session_id']}/requests/7-0/answer",
        json={"decision": "cancel"},
    )
    assert response.status_code == 200
    assert response.json()["data"]["request"] == {
        "request_id": "7-0",
        "status": "answered",
        "decision": "cancel",
    }
    assert hub._codex.answered_requests == [  # noqa: SLF001
        (row["session_id"], "7-0", "cancel")
    ]


def test_post_answer_request_rejects_cc_session(
    client: TestClient, workdir: Path
):
    """CC AskUserQuestion keeps its existing /api/cc answer contract."""

    row = client.post(
        "/api/agent/sessions", json=cc_payload(workdir)
    ).json()["data"]
    response = client.post(
        f"/api/agent/sessions/{row['session_id']}/requests/7-0/answer",
        json={"decision": "cancel"},
    )
    assert response.status_code == 422


def test_get_codex_requests_supports_disconnect_recovery(
    client: TestClient, workdir: Path
):
    """The host-neutral recovery endpoint is available only through a binding."""

    row = client.post(
        "/api/agent/sessions", json=codex_payload(workdir)
    ).json()["data"]
    response = client.get(
        f"/api/agent/sessions/{row['session_id']}/requests"
    )
    assert response.status_code == 200
    assert response.json()["data"] == {"requests": []}


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


# ---------------------------------------------------------------------------
# slice-074: GET /api/agent/sessions/{id}/history — unified envelope
# ---------------------------------------------------------------------------

from trowel_py.schemas.agent_host import AGENT_EVENT_SCHEMA  # noqa: E402
from trowel_py.schemas.cc_host import (  # noqa: E402
    FinishedEvent,
    TextEvent,
    UserEvent,
)


def _put_cc_binding(
    hub: SessionHub, workdir: Path, *, native_session_id: str | None
) -> str:
    """Insert a CC binding directly (no live host) for history-route tests."""

    binding = make_binding(
        session_id="hist-cc",
        runtime=Runtime.CLAUDE_CODE,
        native_session_id=native_session_id,
        workdir=str(workdir),
        model="glm-5.2",
        effort=None,
        permission="bypassPermissions",
        memory_enabled=True,
        profile_enabled=True,
        capabilities=("tools", "approval", "checkpoint"),
        name="proj",
    )
    hub.store.put(binding)
    return binding.session_id


def test_get_history_cc_wraps_into_envelope(
    client: TestClient, hub: SessionHub, workdir: Path, monkeypatch
):
    """CC history replay returns AgentEvent v1 envelopes with fresh seq from 1."""

    sid = _put_cc_binding(hub, workdir, native_session_id="cc-sess-9")
    monkeypatch.setattr(
        "trowel_py.cc_host.history.parse_history",
        lambda workdir, cc_session_id: [
            UserEvent(text="hi"),
            TextEvent(text="hello back"),
            FinishedEvent(usage={}, total_cost_usd=0.001, num_turns=1),
        ],
    )
    resp = client.get(f"/api/agent/sessions/{sid}/history")
    assert resp.status_code == 200
    events = resp.json()["data"]
    assert [e["schema"] for e in events] == [AGENT_EVENT_SCHEMA] * 3
    assert [e["type"] for e in events] == ["user", "text", "finished"]
    assert [e["seq"] for e in events] == [1, 2, 3]  # fresh adapter
    assert all(e["runtime"] == "claude_code" for e in events)


def test_get_history_cc_no_native_returns_empty(
    client: TestClient, hub: SessionHub, workdir: Path
):
    """A CC session that never completed a turn has no native id → empty replay."""

    sid = _put_cc_binding(hub, workdir, native_session_id=None)
    resp = client.get(f"/api/agent/sessions/{sid}/history")
    assert resp.status_code == 200
    assert resp.json()["data"] == []


def test_get_history_codex_not_implemented(
    client: TestClient, hub: SessionHub, workdir: Path
):
    """Codex thread history lands in slice-079; this slice returns a clear 501."""

    binding = make_binding(
        session_id="hist-codex",
        runtime=Runtime.CODEX,
        native_session_id="thr-1",
        workdir=str(workdir),
        model="gpt-5.6-sol",
        effort=None,
        permission=None,
        memory_enabled=True,
        profile_enabled=True,
        capabilities=("tools", "approval"),
        name="proj",
    )
    hub.store.put(binding)
    resp = client.get(
        "/api/agent/sessions/{binding.session_id}/history".format(binding=binding)
    )
    assert resp.status_code == 501


def test_get_history_unknown_session_404(client: TestClient):
    resp = client.get("/api/agent/sessions/nope/history")
    assert resp.status_code == 404


def test_error_envelope_uses_per_session_seq_not_fixed_one(
    client: TestClient, hub: SessionHub, workdir: Path, monkeypatch
):
    """slice-074 gpt5.6 Critical 1: a route-level error after events flowed
    must get the NEXT per-session seq, not a fixed seq=1 (which the frontend
    would drop as a dup of the first event)."""

    sid = _put_cc_binding(hub, workdir, native_session_id="cc-sess-err")
    # Simulate the live adapter having already stamped seq 1..3 (e.g. a prior
    # turn's events). The error envelope must be seq 4, not 1.
    from trowel_py.agent_host.cc_adapter import CcEventAdapter
    from trowel_py.schemas.cc_host import TextEvent

    adapter = CcEventAdapter(sid)
    for _ in range(3):
        adapter.wrap(TextEvent(text="x").model_dump())
    hub._cc_adapters[sid] = adapter  # noqa: SLF001 — seed the live adapter

    envelope = hub.error_envelope(sid, "boom")
    assert envelope["type"] == "error"
    assert envelope["payload"]["errors"] == ["boom"]
    assert envelope["seq"] == 4  # continues the per-session counter, not 1
    assert envelope["runtime"] == "claude_code"
