"""验证桌面实例凭据和 readiness 接口不会影响 browser 模式。"""

import os

from fastapi.testclient import TestClient

from trowel_py.app import create_app


def test_browser_mode_keeps_health_endpoint_open(monkeypatch) -> None:
    """未配置桌面凭据时，现有 browser 请求继续直接访问 API。"""
    monkeypatch.delenv("TROWEL_DESKTOP_CREDENTIAL", raising=False)
    response = TestClient(create_app()).get("/api/health")
    assert response.status_code == 200


def test_desktop_mode_requires_instance_credential(monkeypatch) -> None:
    """配置桌面凭据后，所有 API 都拒绝无凭据和错误凭据。"""
    monkeypatch.setenv("TROWEL_DESKTOP_CREDENTIAL", "desktop-secret")
    client = TestClient(create_app())

    missing = client.get("/api/health")
    wrong = client.get(
        "/api/health", headers={"Authorization": "Bearer another-secret"}
    )

    assert missing.status_code == 401
    assert missing.json() == {
        "success": False,
        "data": None,
        "error": "desktop credential required",
    }
    assert wrong.status_code == 401


def test_readiness_returns_the_started_instance_contract(monkeypatch) -> None:
    """Host 能用凭据核对 sidecar 实例、版本、协议和公开能力。"""
    monkeypatch.setenv("TROWEL_DESKTOP_CREDENTIAL", "desktop-secret")
    monkeypatch.setenv("TROWEL_APP_INSTANCE_ID", "instance-123")
    client = TestClient(create_app())

    response = client.get(
        "/api/desktop/readiness",
        headers={"Authorization": "Bearer desktop-secret"},
    )

    assert response.status_code == 200
    assert response.json() == {
        "success": True,
        "data": {
            "status": "ready",
            "app_version": "0.1.0",
            "protocol_version": 1,
            "instance_id": "instance-123",
            "capabilities": ["agent", "memory", "review"],
        },
        "error": None,
    }
    assert "TROWEL_DESKTOP_CREDENTIAL" not in os.environ
    assert "TROWEL_APP_INSTANCE_ID" not in os.environ


def test_desktop_mode_allows_only_the_host_renderer_origin(monkeypatch) -> None:
    """随机 Vite 端口可通过预检，其他网页来源仍拿不到 sidecar 响应。"""
    monkeypatch.setenv("TROWEL_DESKTOP_CREDENTIAL", "desktop-secret")
    monkeypatch.setenv(
        "TROWEL_DESKTOP_RENDERER_ORIGIN", "http://127.0.0.1:43124"
    )
    client = TestClient(create_app())
    headers = {
        "Access-Control-Request-Method": "GET",
        "Access-Control-Request-Headers": "Authorization",
    }

    allowed = client.options(
        "/api/health",
        headers={**headers, "Origin": "http://127.0.0.1:43124"},
    )
    rejected = client.options(
        "/api/health",
        headers={**headers, "Origin": "https://attacker.example"},
    )

    assert allowed.status_code == 200
    assert allowed.headers["access-control-allow-origin"] == "http://127.0.0.1:43124"
    assert rejected.status_code == 400
