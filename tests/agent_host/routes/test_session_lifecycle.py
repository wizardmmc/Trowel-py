from pathlib import Path

from fastapi.testclient import TestClient

from tests.agent_host.routes.support import (
    cc_payload,
    codex_payload,
    create_session,
)


def test_post_sessions_creates_cc(client: TestClient, workdir: Path) -> None:
    response = client.post("/api/agent/sessions", json=cc_payload(workdir))

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["runtime"] == "claude_code"
    assert data["session_id"]


def test_post_sessions_creates_codex(client: TestClient, workdir: Path) -> None:
    response = client.post("/api/agent/sessions", json=codex_payload(workdir))

    assert response.status_code == 200
    assert response.json()["data"]["runtime"] == "codex"


def test_post_sessions_missing_workdir_400(
    client: TestClient,
    tmp_path: Path,
) -> None:
    missing = tmp_path / "missing"
    response = client.post(
        "/api/agent/sessions",
        json=cc_payload(missing),
    )

    assert response.status_code == 400


def test_post_sessions_invalid_runtime_422(
    client: TestClient,
    workdir: Path,
) -> None:
    response = client.post(
        "/api/agent/sessions",
        json={"runtime": "gemini", "workdir": str(workdir)},
    )

    assert response.status_code == 422


def test_get_active_lists_mixed(
    client: TestClient,
    workdir: Path,
) -> None:
    create_session(client, cc_payload(workdir))
    create_session(client, codex_payload(workdir))

    response = client.get("/api/agent/sessions/active")
    assert response.status_code == 200
    sessions = response.json()["data"]["sessions"]
    assert {session["runtime"] for session in sessions} == {
        "claude_code",
        "codex",
    }


def test_post_activate_sets_active(
    client: TestClient,
    workdir: Path,
) -> None:
    first = create_session(client, cc_payload(workdir))
    second = create_session(client, codex_payload(workdir))

    response = client.post(f"/api/agent/sessions/{first['session_id']}/activate")
    assert response.status_code == 200
    assert response.json()["data"]["active_id"] == first["session_id"]
    active = client.get("/api/agent/sessions/active").json()["data"]
    assert active["active_id"] == first["session_id"]
    # 切换活动会话不能销毁其他会话。
    assert second["session_id"] in {
        session["session_id"] for session in active["sessions"]
    }


def test_get_session(client: TestClient, workdir: Path) -> None:
    created = create_session(client, cc_payload(workdir))
    response = client.get(f"/api/agent/sessions/{created['session_id']}")
    assert response.status_code == 200
    assert response.json()["data"]["session_id"] == created["session_id"]


def test_get_session_404(client: TestClient) -> None:
    response = client.get("/api/agent/sessions/unknown")

    assert response.status_code == 404


def test_delete_session(client: TestClient, workdir: Path) -> None:
    created = create_session(client, cc_payload(workdir))
    response = client.delete(f"/api/agent/sessions/{created['session_id']}")
    assert response.status_code == 200
    assert response.json()["data"]["closed"] is True
    assert client.get(f"/api/agent/sessions/{created['session_id']}").status_code == 404
