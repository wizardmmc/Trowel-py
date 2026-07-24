"""验证真实 ``create_app`` 装配 Agent Host；写入路径由全局 fixture 隔离。"""

from __future__ import annotations

from fastapi.testclient import TestClient

from trowel_py.app import create_app


def test_agent_router_mounted_and_hub_initialized():
    app = create_app()
    with TestClient(app) as client:
        resp = client.get("/api/agent/runtimes")
        assert resp.status_code == 200
        runtimes = resp.json()["data"]
        assert {r["runtime"] for r in runtimes} == {"claude_code", "codex"}
        assert client.get("/api/cc/models").status_code == 200


def test_agent_sessions_active_starts_empty():
    app = create_app()
    with TestClient(app) as client:
        resp = client.get("/api/agent/sessions/active")
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["sessions"] == []
        assert data["active_id"] is None


def test_agent_hub_attached_to_app_state():
    app = create_app()
    with TestClient(app):
        assert app.state.agent_hub is not None
        assert app.state.agent_hub.codex_available == (
            app.state.codex_host_manager is not None
        )
