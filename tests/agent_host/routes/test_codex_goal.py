from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from tests.agent_host.hub._support import FakeCodexManager
from tests.agent_host.routes.support import codex_payload, create_session
from trowel_py.agent_host.hub import SessionHub


def test_codex_goal_get_set_and_clear(client: TestClient, workdir: Path) -> None:
    created = create_session(client, codex_payload(workdir))
    sid = created["session_id"]

    empty = client.get(f"/api/agent/sessions/{sid}/goal")
    updated = client.put(
        f"/api/agent/sessions/{sid}/goal",
        json={
            "objective": "Ship Goal and Plan",
            "status": "active",
            "token_budget": 12000,
        },
    )
    cleared = client.delete(f"/api/agent/sessions/{sid}/goal")

    assert empty.status_code == 200
    assert empty.json()["data"]["goal"] is None
    persisted = client.get(f"/api/agent/sessions/{sid}").json()["data"]
    assert persisted["native_session_id"] == "thread-1"
    assert updated.status_code == 200
    assert updated.json()["data"]["goal"]["objective"] == "Ship Goal and Plan"
    assert updated.json()["data"]["goal"]["tokenBudget"] == 12000
    assert cleared.status_code == 200
    assert cleared.json()["data"] == {"cleared": True}


def test_goal_api_rejects_claude_code(client: TestClient, workdir: Path) -> None:
    created = create_session(
        client, {"runtime": "claude_code", "workdir": str(workdir)}
    )

    response = client.get(f"/api/agent/sessions/{created['session_id']}/goal")

    assert response.status_code == 422


def test_codex_turn_start_is_decoupled_from_live_sse(
    client: TestClient, workdir: Path
) -> None:
    created = create_session(client, codex_payload(workdir))

    response = client.post(
        f"/api/agent/sessions/{created['session_id']}/turns",
        json={"text": "continue"},
    )

    assert response.status_code == 200
    assert response.json()["data"] == {"turn_id": "fake-turn-id"}


def test_codex_turn_start_conflicts_with_goal_continuation(
    client: TestClient, workdir: Path, hub: SessionHub
) -> None:
    created = create_session(client, codex_payload(workdir))
    sid = created["session_id"]
    assert client.get(f"/api/agent/sessions/{sid}/goal").status_code == 200
    manager = hub._codex  # noqa: SLF001
    assert isinstance(manager, FakeCodexManager)
    session = manager.sessions[sid]
    session.record_native_turn_started("turn-auto")

    response = client.post(
        f"/api/agent/sessions/{sid}/turns", json={"text": "race"}
    )

    assert response.status_code == 409
