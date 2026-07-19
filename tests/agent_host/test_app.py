"""slice-072: app.py wires ``/api/agent/*`` and bootstraps the SessionHub.

These hit the real ``create_app()`` (lifespan included) so they verify the
router is mounted and the Hub is constructed at startup. The autouse conftest
pins every home-writing path to tmp, so the real ``~/.trowel`` is untouched.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from trowel_py.app import create_app


def test_agent_router_mounted_and_hub_initialized():
    """``/api/agent/runtimes`` answers and both runtimes are declared."""

    app = create_app()
    with TestClient(app) as client:
        resp = client.get("/api/agent/runtimes")
        assert resp.status_code == 200
        runtimes = resp.json()["data"]
        assert {r["runtime"] for r in runtimes} == {"claude_code", "codex"}
        # C-5 regression guard: the legacy CC route is untouched.
        assert client.get("/api/cc/models").status_code == 200


def test_agent_sessions_active_starts_empty():
    """A fresh process has no live sessions and no active id."""

    app = create_app()
    with TestClient(app) as client:
        resp = client.get("/api/agent/sessions/active")
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["sessions"] == []
        assert data["active_id"] is None


def test_agent_hub_attached_to_app_state():
    """The Hub is constructed at startup and reachable via app.state."""

    app = create_app()
    with TestClient(app):
        assert app.state.agent_hub is not None
        assert (
            app.state.agent_hub.codex_available
            == (app.state.codex_host_manager is not None)
        )
