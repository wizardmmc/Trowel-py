from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tests.agent_host.routes.support import put_cc_binding
from trowel_py.agent_host.binding import Runtime, make_binding
from trowel_py.agent_host.cc_adapter import CcEventAdapter
from trowel_py.agent_host.hub import SessionHub
from trowel_py.schemas.agent_host import AGENT_EVENT_SCHEMA
from trowel_py.schemas.cc_host import FinishedEvent, TextEvent, UserEvent


def test_get_history_cc_wraps_into_envelope(
    client: TestClient,
    hub: SessionHub,
    workdir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session_id = put_cc_binding(
        hub,
        workdir,
        native_session_id="cc-native-9",
    )
    monkeypatch.setattr(
        "trowel_py.cc_host.history.parse_history",
        lambda workdir, cc_session_id: [
            UserEvent(text="hi"),
            TextEvent(text="hello back"),
            FinishedEvent(usage={}, total_cost_usd=0.001, num_turns=1),
        ],
    )

    response = client.get(f"/api/agent/sessions/{session_id}/history")

    assert response.status_code == 200
    events = response.json()["data"]
    assert [event["schema"] for event in events] == [AGENT_EVENT_SCHEMA] * 3
    assert [event["type"] for event in events] == [
        "user",
        "text",
        "finished",
    ]
    assert [event["seq"] for event in events] == [1, 2, 3]
    assert all(event["runtime"] == "claude_code" for event in events)


def test_get_history_cc_no_native_returns_empty(
    client: TestClient,
    hub: SessionHub,
    workdir: Path,
) -> None:
    session_id = put_cc_binding(
        hub,
        workdir,
        native_session_id=None,
    )

    response = client.get(f"/api/agent/sessions/{session_id}/history")

    assert response.status_code == 200
    assert response.json()["data"] == []


def test_get_history_codex_not_implemented(
    client: TestClient,
    hub: SessionHub,
    workdir: Path,
) -> None:
    binding = make_binding(
        session_id="history-codex",
        runtime=Runtime.CODEX,
        native_session_id="thread-1",
        workdir=str(workdir),
        model="gpt-5.6-sol",
        effort=None,
        permission=None,
        memory_enabled=True,
        profile_enabled=True,
        capabilities=("tools", "approval"),
        name="project",
    )
    hub.store.put(binding)

    response = client.get(f"/api/agent/sessions/{binding.session_id}/history")

    assert response.status_code == 501


def test_get_history_unknown_session_404(client: TestClient) -> None:
    response = client.get("/api/agent/sessions/unknown/history")

    assert response.status_code == 404


def test_error_envelope_uses_per_session_seq_not_fixed_one(
    hub: SessionHub,
    workdir: Path,
) -> None:
    session_id = put_cc_binding(
        hub,
        workdir,
        native_session_id="cc-native-error",
    )
    adapter = CcEventAdapter(session_id)
    for _ in range(3):
        adapter.wrap(TextEvent(text="x").model_dump())
    hub._cc_adapters[session_id] = adapter  # noqa: SLF001

    envelope = hub.error_envelope(session_id, "boom")

    assert envelope["type"] == "error"
    assert envelope["payload"]["errors"] == ["boom"]
    assert envelope["seq"] == 4
    assert envelope["runtime"] == "claude_code"
