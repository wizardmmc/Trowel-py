from __future__ import annotations

import pytest

from trowel_py.model_os.waking.runtime_bridge import observe_runtime_event


class FakeCoordinator:
    def __init__(self) -> None:
        self.calls = []

    async def observe(self, session_id, payload, *, generation):
        self.calls.append(("observe", session_id, payload["type"], generation))
        return "observed"

    async def connection_lost(self, session_id, *, generation):
        self.calls.append(("connection_lost", session_id, generation))
        return "lost"


@pytest.mark.anyio
@pytest.mark.parametrize(
    "payload",
    [
        {"type": "session_exited", "payload": {}},
        {"type": "error", "payload": {"subclass": "host_error"}},
    ],
)
async def test_host_terminal_also_closes_pending_channel(payload) -> None:
    coordinator = FakeCoordinator()

    result = await observe_runtime_event(
        coordinator,
        "session-1",
        payload,
        generation="generation-1",
    )

    assert result == "lost"
    assert [call[0] for call in coordinator.calls] == ["observe", "connection_lost"]


@pytest.mark.anyio
async def test_normal_terminal_does_not_report_connection_loss() -> None:
    coordinator = FakeCoordinator()

    result = await observe_runtime_event(
        coordinator,
        "session-1",
        {"type": "finished", "payload": {}},
        generation="generation-1",
    )

    assert result == "observed"
    assert [call[0] for call in coordinator.calls] == ["observe"]
