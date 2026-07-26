from __future__ import annotations

import asyncio
from collections.abc import Mapping
from dataclasses import replace

import pytest

from tests.model_os._episode_helpers import (
    activate_episode,
    make_running_system_episode,
)
from trowel_py.model_os.context_observer import ContextConfidence
from trowel_py.model_os.store import ModelOsStore
from trowel_py.model_os.types import EpisodeStatus, SnapshotSource
from trowel_py.model_os.yielding import (
    TurnRegistration,
    YieldCoordinator,
    YieldProposal,
    YieldSuggestedState,
)
from trowel_py.model_os.yielding.models import SoftYieldPolicy


class Runtime:
    def __init__(self) -> None:
        self.steers: list[tuple[str, str, str, str]] = []
        self.interrupts: list[str] = []
        self.releases: list[str] = []
        self.steer_error = False

    async def steer(
        self,
        session_id: str,
        text: str,
        *,
        expected_turn_id: str,
        expected_generation: str,
    ) -> None:
        self.steers.append(
            (session_id, text, expected_turn_id, expected_generation)
        )
        if self.steer_error:
            raise RuntimeError("steer outcome unknown")

    async def interrupt(self, session_id: str) -> None:
        self.interrupts.append(session_id)

    async def release(self, work_lease_id: str) -> None:
        self.releases.append(work_lease_id)


def _registration(episode_id: str, lease, *, runtime: str) -> TurnRegistration:
    return TurnRegistration(
        session_id="session-1",
        episode_id=episode_id,
        runtime=runtime,
        turn_id="turn-1",
        generation="runtime-generation-1",
        native_session_id="native-1",
        ownership_lease_id=lease.lease_id,
        ownership_owner=lease.owner,
        ownership_token=lease.fencing_token,
        work_lease_id="work-lease-1",
        context_generation=0,
    )


def _cc_usage(used_tokens: int) -> dict[str, object]:
    return {
        "session_id": "session-1",
        "runtime": "claude_code",
        "seq": 1,
        "type": "context_usage",
        "turn_id": "turn-1",
        "payload": {
            "message_id": f"message-{used_tokens}",
            "model": "glm-5.2",
            "usage": {
                "input_tokens": used_tokens,
                "cache_creation_input_tokens": 0,
                "cache_read_input_tokens": 0,
                "output_tokens": 0,
            },
        },
    }


def _codex_usage(used_tokens: int) -> dict[str, object]:
    return {
        "session_id": "session-1",
        "runtime": "codex",
        "seq": 1,
        "type": "usage_updated",
        "turn_id": "turn-1",
        "payload": {
            "last": {"totalTokens": used_tokens},
            "total": {"totalTokens": used_tokens},
            "model_context_window": 200_000,
        },
    }


def _event(event_type: str, payload: Mapping[str, object] | None = None) -> dict:
    return {
        "session_id": "session-1",
        "runtime": "claude_code",
        "seq": 2,
        "type": event_type,
        "turn_id": "turn-1",
        "payload": dict(payload or {}),
    }


def _setup(
    store: ModelOsStore,
    *,
    runtime_name: str = "claude_code",
    deadline_seconds: float = 120.0,
):
    episode, lease, _ = make_running_system_episode(store)
    activate_episode(store, episode.episode_id, lease)
    runtime = Runtime()
    coordinator = YieldCoordinator(
        store,
        interrupt_runtime=runtime.interrupt,
        steer_runtime=runtime.steer,
        release_work_lease=runtime.release,
        soft_policy=SoftYieldPolicy(
            threshold_ratio=0.8,
            deadline_seconds=deadline_seconds,
            policy_version="yield-soft-test-v1",
        ),
    )
    return episode, lease, runtime, coordinator, _registration(
        episode.episode_id, lease, runtime=runtime_name
    )


@pytest.mark.anyio
async def test_reliable_usage_below_80_percent_is_persisted_without_steer(
    store: ModelOsStore,
) -> None:
    episode, _, runtime, coordinator, registration = _setup(store)
    await coordinator.register_turn(registration)

    receipt = await coordinator.observe(
        "session-1",
        _cc_usage(159_999),
        generation="runtime-generation-1",
    )

    assert receipt is None
    assert runtime.steers == []
    latest = store.read_snapshot().latest_context_sample(episode.episode_id)
    assert latest is not None
    assert latest.latest_sample.used_tokens == 159_999
    assert latest.latest_sample.confidence is ContextConfidence.RELIABLE


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("runtime_name", "event"),
    [
        ("claude_code", _cc_usage(160_000)),
        ("codex", _codex_usage(160_000)),
    ],
)
async def test_80_percent_dispatches_one_soft_request_with_runtime_cas(
    store: ModelOsStore,
    runtime_name: str,
    event: dict[str, object],
) -> None:
    _, _, runtime, coordinator, registration = _setup(
        store, runtime_name=runtime_name
    )
    await coordinator.register_turn(registration)

    receipt = await coordinator.observe(
        "session-1", event, generation="runtime-generation-1"
    )
    duplicate = await coordinator.observe(
        "session-1", event, generation="runtime-generation-1"
    )

    assert receipt is not None and receipt.status == "soft_request_sent"
    assert duplicate is None
    assert len(runtime.steers) == 1
    session_id, text, turn_id, generation = runtime.steers[0]
    assert (session_id, turn_id, generation) == (
        "session-1",
        "turn-1",
        "runtime-generation-1",
    )
    assert "TROWEL_KERNEL_SOFT_YIELD" in text
    expected_tool = (
        "mcp__trowel_model_os__yield"
        if runtime_name == "claude_code"
        else "trowel_model_os.yield"
    )
    assert expected_tool in text
    assert "context_generation=0" in text
    await coordinator.close()


@pytest.mark.anyio
async def test_cc_terminal_usage_triggers_soft_request_on_next_turn(
    store: ModelOsStore,
) -> None:
    episode, _, runtime, coordinator, registration = _setup(store)
    await coordinator.register_turn(registration)
    await coordinator.observe(
        "session-1", _cc_usage(0), generation="runtime-generation-1"
    )

    terminal = await coordinator.observe(
        "session-1",
        _event(
            "finished",
            {
                "usage": {
                    "input_tokens": 160_000,
                    "cache_creation_input_tokens": 0,
                    "cache_read_input_tokens": 0,
                    "output_tokens": 0,
                }
            },
        ),
        generation="runtime-generation-1",
    )

    assert terminal is None
    assert runtime.steers == []
    latest = store.read_snapshot().latest_context_sample(episode.episode_id)
    assert latest is not None and latest.latest_sample.used_tokens == 160_000

    await coordinator.register_turn(replace(registration, turn_id="turn-2"))

    assert runtime.steers == []
    await coordinator.observe(
        "session-1", _cc_usage(0), generation="runtime-generation-1"
    )
    assert runtime.steers == []
    await coordinator.observe(
        "session-1",
        _event("tool_call", {"tool_use_id": "tool-1"}),
        generation="runtime-generation-1",
    )

    assert len(runtime.steers) == 1
    assert runtime.steers[0][2] == "turn-2"
    await coordinator.close()


@pytest.mark.anyio
async def test_soft_request_proposal_and_terminal_commit_cooperative_snapshot(
    store: ModelOsStore,
) -> None:
    episode, _, runtime, coordinator, registration = _setup(store)
    await coordinator.register_turn(registration)
    await coordinator.observe(
        "session-1", _cc_usage(160_000), generation="runtime-generation-1"
    )

    proposal = await coordinator.propose(
        "session-1",
        YieldProposal(
            reason="context safety line",
            suggested_task_state=YieldSuggestedState.READY,
            waiting_condition=None,
            current_judgment="关键 diff 与测试已经核对",
            next_steps=("fresh 后读取 snapshot",),
            continue_same_task=True,
        ),
    )
    terminal = await coordinator.observe(
        "session-1",
        _event("finished"),
        generation="runtime-generation-1",
    )

    assert proposal.status == "registered"
    assert terminal is not None and terminal.status == "closed"
    state = store.read_snapshot().episode_by_id(episode.episode_id)
    assert state is not None and state.status is EpisodeStatus.CLOSED
    snapshot = store.read_episode_snapshot(state.last_snapshot_ref)
    assert snapshot.source is SnapshotSource.COOPERATIVE
    assert runtime.interrupts == []


@pytest.mark.anyio
async def test_soft_deadline_falls_back_to_correlated_forced_interrupt(
    store: ModelOsStore,
) -> None:
    episode, _, runtime, coordinator, registration = _setup(
        store, deadline_seconds=0.01
    )
    await coordinator.register_turn(registration)
    await coordinator.observe(
        "session-1", _cc_usage(160_000), generation="runtime-generation-1"
    )

    await asyncio.sleep(0.05)

    assert runtime.interrupts == ["session-1"]
    terminal = await coordinator.observe(
        "session-1",
        _event("interrupted", {"status": "interrupted"}),
        generation="runtime-generation-1",
    )
    assert terminal is not None and terminal.status == "closed"
    state = store.read_snapshot().episode_by_id(episode.episode_id)
    assert state is not None
    snapshot = store.read_episode_snapshot(state.last_snapshot_ref)
    assert snapshot.source is SnapshotSource.RECOVERY_PARTIAL


@pytest.mark.anyio
async def test_native_compaction_supersedes_soft_request_and_prompted_proposal(
    store: ModelOsStore,
) -> None:
    episode, _, runtime, coordinator, registration = _setup(
        store, deadline_seconds=0.01
    )
    await coordinator.register_turn(registration)
    await coordinator.observe(
        "session-1", _cc_usage(160_000), generation="runtime-generation-1"
    )
    await coordinator.propose(
        "session-1",
        YieldProposal(
            reason="context safety line",
            suggested_task_state=YieldSuggestedState.READY,
            waiting_condition=None,
            current_judgment="准备交接",
            next_steps=("继续",),
            continue_same_task=True,
        ),
    )

    compact = await coordinator.observe(
        "session-1",
        _event("compact_boundary", {"trigger": "auto"}),
        generation="runtime-generation-1",
    )
    terminal = await coordinator.observe(
        "session-1", _event("finished"), generation="runtime-generation-1"
    )
    await asyncio.sleep(0.03)

    assert compact is not None and compact.status == "soft_request_superseded"
    assert terminal is None
    assert runtime.interrupts == []
    state = store.read_snapshot().episode_by_id(episode.episode_id)
    assert state is not None and state.status is EpisodeStatus.ACTIVE
    assert state.last_snapshot_ref is None
    assert coordinator.context_generation("session-1") == 1
    await coordinator.close()


@pytest.mark.anyio
async def test_unknown_soft_steer_is_not_retried_before_deadline_interrupt(
    store: ModelOsStore,
) -> None:
    _, _, runtime, coordinator, registration = _setup(
        store, deadline_seconds=0.01
    )
    runtime.steer_error = True
    await coordinator.register_turn(registration)

    first = await coordinator.observe(
        "session-1", _cc_usage(160_000), generation="runtime-generation-1"
    )
    duplicate = await coordinator.observe(
        "session-1", _cc_usage(160_000), generation="runtime-generation-1"
    )
    await asyncio.sleep(0.05)

    assert first is not None and first.status == "soft_request_unknown"
    assert duplicate is None
    assert len(runtime.steers) == 1
    assert runtime.interrupts == ["session-1"]
    kinds = {item.kind for item in store.read_journal_page(limit=100).items}
    assert "command.unknown" in kinds


@pytest.mark.anyio
async def test_terminal_without_prompted_proposal_closes_with_partial_snapshot(
    store: ModelOsStore,
) -> None:
    episode, _, _, coordinator, registration = _setup(store)
    await coordinator.register_turn(registration)
    await coordinator.observe(
        "session-1", _cc_usage(160_000), generation="runtime-generation-1"
    )

    terminal = await coordinator.observe(
        "session-1", _event("finished"), generation="runtime-generation-1"
    )

    assert terminal is not None and terminal.status == "closed"
    state = store.read_snapshot().episode_by_id(episode.episode_id)
    assert state is not None
    snapshot = store.read_episode_snapshot(state.last_snapshot_ref)
    assert snapshot.source is SnapshotSource.RECOVERY_PARTIAL


@pytest.mark.anyio
async def test_codex_completed_compaction_advances_generation_and_supersedes(
    store: ModelOsStore,
) -> None:
    episode, _, runtime, coordinator, registration = _setup(
        store, runtime_name="codex", deadline_seconds=0.01
    )
    await coordinator.register_turn(registration)
    await coordinator.observe(
        "session-1", _codex_usage(160_000), generation="runtime-generation-1"
    )
    compact_event = {
        "session_id": "session-1",
        "runtime": "codex",
        "seq": 2,
        "type": "compaction",
        "turn_id": "turn-1",
        "payload": {"phase": "completed"},
    }

    compact = await coordinator.observe(
        "session-1", compact_event, generation="runtime-generation-1"
    )
    await asyncio.sleep(0.03)

    assert compact is not None and compact.status == "soft_request_superseded"
    assert coordinator.context_generation("session-1") == 1
    assert runtime.interrupts == []
    state = store.read_snapshot().episode_by_id(episode.episode_id)
    assert state is not None and state.status is EpisodeStatus.ACTIVE
    await coordinator.close()


@pytest.mark.anyio
async def test_compaction_cancels_expired_but_not_dispatched_context_interrupt(
    store: ModelOsStore,
) -> None:
    episode, _, runtime, coordinator, registration = _setup(
        store, deadline_seconds=0.01
    )
    await coordinator.register_turn(registration)
    await coordinator.observe(
        "session-1",
        _event("text", {"text": "still streaming"}),
        generation="runtime-generation-1",
    )
    await coordinator.observe(
        "session-1", _cc_usage(160_000), generation="runtime-generation-1"
    )
    await asyncio.sleep(0.03)

    compact = await coordinator.observe(
        "session-1",
        _event("compact_boundary", {"trigger": "auto"}),
        generation="runtime-generation-1",
    )
    terminal = await coordinator.observe(
        "session-1", _event("finished"), generation="runtime-generation-1"
    )

    assert compact is not None and compact.status == "soft_request_superseded"
    assert terminal is None
    assert runtime.interrupts == []
    state = store.read_snapshot().episode_by_id(episode.episode_id)
    assert state is not None and state.status is EpisodeStatus.ACTIVE
    assert state.last_snapshot_ref is None
