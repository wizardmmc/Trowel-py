from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from tests.agent_host.hub._support import FakeCodexManager
from tests.agent_host.routes.support import (
    cc_payload,
    codex_payload,
    create_session,
    parse_sse,
)
from trowel_py.agent_host.hub import SessionHub


def test_post_interrupt(client: TestClient, workdir: Path) -> None:
    created = create_session(client, cc_payload(workdir))
    response = client.post(f"/api/agent/sessions/{created['session_id']}/interrupt")

    assert response.status_code == 200
    assert response.json()["data"]["interrupted"] is True


def test_post_answer_codex_request_routes_by_session(
    client: TestClient,
    workdir: Path,
    hub: SessionHub,
) -> None:
    created = create_session(client, codex_payload(workdir))
    response = client.post(
        f"/api/agent/sessions/{created['session_id']}/requests/7-0/answer",
        json={"decision": "cancel"},
    )

    assert response.status_code == 200
    assert response.json()["data"]["request"] == {
        "request_id": "7-0",
        "status": "answered",
        "decision": "cancel",
    }
    manager = hub._codex  # noqa: SLF001
    assert isinstance(manager, FakeCodexManager)
    assert manager.answered_requests == [(created["session_id"], "7-0", "cancel")]


def test_post_answer_request_rejects_cc_session(
    client: TestClient,
    workdir: Path,
) -> None:
    # CC 问答继续使用专用接口，不由统一 Codex request 端点接管。
    created = create_session(client, cc_payload(workdir))
    response = client.post(
        f"/api/agent/sessions/{created['session_id']}/requests/7-0/answer",
        json={"decision": "cancel"},
    )

    assert response.status_code == 422


def test_get_codex_requests_supports_disconnect_recovery(
    client: TestClient,
    workdir: Path,
    hub: SessionHub,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    created = create_session(client, codex_payload(workdir))
    retained = {
        "request_id": "7-0",
        "session_id": created["session_id"],
        "thread_id": "thread-1",
        "turn_id": "turn-1",
        "item_id": "item-1",
        "approval_kind": "command_approval",
        "command": "git status",
        "cwd": str(workdir),
        "reason": "inspect repository state",
        "available_decisions": ["accept", "cancel"],
        "status": "pending",
        "decision": None,
        "auto_resolved": False,
        "resolution_reason": None,
    }
    seen_sessions: list[str] = []
    manager = hub._codex  # noqa: SLF001
    assert isinstance(manager, FakeCodexManager)

    def list_requests(session_id: str) -> list[SimpleNamespace]:
        seen_sessions.append(session_id)
        return [SimpleNamespace(to_payload=lambda: retained)]

    monkeypatch.setattr(manager, "list_requests", list_requests)
    response = client.get(f"/api/agent/sessions/{created['session_id']}/requests")

    assert response.status_code == 200
    assert response.json()["data"] == {"requests": [retained]}
    assert seen_sessions == [created["session_id"]]


def test_post_messages_streams_sse(
    client: TestClient,
    workdir: Path,
) -> None:
    created = create_session(client, cc_payload(workdir))
    with client.stream(
        "POST",
        f"/api/agent/sessions/{created['session_id']}/messages",
        json={"text": "hi"},
    ) as response:
        assert response.status_code == 200
        body = b"".join(response.iter_bytes())

    events = parse_sse(body)
    assert len(events) == 1
    assert events[0]["type"] == "text"
    assert events[0]["runtime"] == "claude_code"
    assert events[0]["payload"]["text"] == "echo:hi"


def test_post_messages_unknown_session_emits_error_frame(
    client: TestClient,
) -> None:
    with client.stream(
        "POST",
        "/api/agent/sessions/unknown/messages",
        json={"text": "hi"},
    ) as response:
        body = b"".join(response.iter_bytes())

    events = parse_sse(body)
    assert len(events) == 1
    assert events[0]["type"] == "error"
    assert events[0]["payload"]["errors"]
