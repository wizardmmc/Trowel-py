from __future__ import annotations

import pytest

from tests.model_os._episode_helpers import make_running_system_episode
from tests.model_os.episode_starting.test_coordinator import FakeBroker
from trowel_py.model_os.episode_starting.command_gate import ModelOsCommandGate
from trowel_py.model_os.types import EventKind
from trowel_py.model_os.work_broker import ModelTier


class FakeYield:
    def __init__(self) -> None:
        self.registrations = []
        self.interrupts = []

    async def register_turn(self, registration) -> None:
        self.registrations.append(registration)

    async def request_forced(self, session_id, reason, **expected):
        self.interrupts.append((session_id, reason, expected))


class FakeHub:
    def __init__(self, identity) -> None:
        self.identity = identity

    def native_identity(self, session_id):
        assert session_id == self.identity.agent_session_id
        return self.identity

    def current_turn_id(self, session_id):
        assert session_id == self.identity.agent_session_id
        return "turn-live"


def _managed_episode(store):
    episode, ownership, _ = make_running_system_episode(store)
    binding = store.bind_episode_runtime(
        episode.episode_id,
        expected_lease_id=ownership.lease_id,
        expected_owner=ownership.owner,
        expected_token=ownership.fencing_token,
        agent_session_id="agent-1",
        runtime="codex",
        native_session_id="thread-1",
        runtime_generation="codex-connection-1",
        runtime_pid=100,
        runtime_pgid=None,
        correlation_id="command.start.1",
        activate=True,
    )
    return episode, ownership, binding


@pytest.mark.anyio
async def test_managed_send_writes_intent_before_turn_and_registers_it(store) -> None:
    episode, ownership, binding = _managed_episode(store)
    broker = FakeBroker()
    yielding = FakeYield()
    gate = ModelOsCommandGate(store, broker=broker, yield_coordinator=yielding)

    ticket = gate.before_send("agent-1", "private prompt is not journaled")
    kinds = [event.kind for _, event in store.list_events()]
    assert kinds[-1] == EventKind.COMMAND_INTENT
    assert "private prompt" not in str(store.list_events())

    await gate.observe_send_event(
        ticket,
        {
            "type": "turn_start",
            "turn_id": "turn-2",
            "session_id": "agent-1",
            "payload": {},
        },
        hub=FakeHub(binding),
    )
    assert yielding.registrations[0].turn_id == "turn-2"
    assert yielding.registrations[0].ownership_token == ownership.fencing_token
    assert broker.started == [("work-lease-1", 1)]


@pytest.mark.anyio
async def test_managed_interrupt_uses_l06_expected_turn_and_generation(store) -> None:
    _, _, binding = _managed_episode(store)
    yielding = FakeYield()
    gate = ModelOsCommandGate(store, broker=FakeBroker(), yield_coordinator=yielding)

    handled = await gate.interrupt("agent-1", hub=FakeHub(binding))

    assert handled is True
    assert yielding.interrupts[0][2] == {
        "expected_turn_id": "turn-live",
        "expected_generation": "codex-connection-1",
    }


def test_unmanaged_session_is_left_to_legacy_hub(store) -> None:
    gate = ModelOsCommandGate(store, broker=FakeBroker(), yield_coordinator=FakeYield())

    assert gate.before_send("ordinary-session", "hello") is None


def test_managed_send_releases_work_lease_when_intent_write_fails(
    store, monkeypatch
) -> None:
    _managed_episode(store)
    broker = FakeBroker()
    gate = ModelOsCommandGate(store, broker=broker, yield_coordinator=FakeYield())

    def fail_intent(*_args, **_kwargs):
        raise RuntimeError("journal unavailable")

    monkeypatch.setattr(store, "append_decision_with_intent", fail_intent)

    with pytest.raises(RuntimeError, match="journal unavailable"):
        gate.before_send("agent-1", "hello")

    assert broker.released == [("work-lease-1", 1)]


def test_managed_send_reuses_episode_route_tier(store, monkeypatch) -> None:
    _managed_episode(store)
    broker = FakeBroker()
    monkeypatch.setattr(
        "trowel_py.model_os.episode_starting.command_gate.route_tier_for_episode",
        lambda _store, _episode_id: ModelTier.FAST,
    )
    gate = ModelOsCommandGate(store, broker=broker, yield_coordinator=FakeYield())

    gate.before_send("agent-1", "continue")

    assert broker.requests[0].model_tier is ModelTier.FAST
