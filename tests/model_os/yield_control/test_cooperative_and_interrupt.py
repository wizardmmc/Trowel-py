from __future__ import annotations

from collections.abc import Mapping

import pytest

from tests.model_os._episode_helpers import (
    activate_episode,
    make_running_system_episode,
)
from trowel_py.model_os.store import ModelOsStore
from trowel_py.model_os.types import EpisodeStatus, SnapshotSource
from trowel_py.model_os.yielding import (
    ForceYieldReason,
    TurnRegistration,
    YieldCoordinator,
    YieldProposal,
    YieldSuggestedState,
)


class FakeRuntime:
    def __init__(self) -> None:
        self.interrupts: list[str] = []
        self.releases: list[str] = []

    async def interrupt(self, session_id: str) -> None:
        self.interrupts.append(session_id)

    async def release(self, work_lease_id: str) -> None:
        self.releases.append(work_lease_id)


def _registration(episode_id: str, lease, *, runtime: str = "codex") -> TurnRegistration:
    return TurnRegistration(
        session_id="session-1",
        episode_id=episode_id,
        runtime=runtime,
        turn_id="turn-1",
        generation="generation-1",
        native_session_id="native-1",
        ownership_lease_id=lease.lease_id,
        ownership_owner=lease.owner,
        ownership_token=lease.fencing_token,
        work_lease_id="work-lease-1",
    )


def _event(
    event_type: str,
    *,
    payload: Mapping[str, object] | None = None,
    item_id: str | None = None,
) -> dict[str, object]:
    return {
        "session_id": "session-1",
        "runtime": "codex",
        "seq": 1,
        "type": event_type,
        "turn_id": "turn-1",
        "item_id": item_id,
        "payload": dict(payload or {}),
    }


@pytest.mark.anyio
async def test_cooperative_yield_checkpoints_only_after_terminal(
    store: ModelOsStore,
) -> None:
    episode, lease, _ = make_running_system_episode(store)
    activate_episode(store, episode.episode_id, lease)
    runtime = FakeRuntime()
    coordinator = YieldCoordinator(
        store,
        interrupt_runtime=runtime.interrupt,
        release_work_lease=runtime.release,
    )
    await coordinator.register_turn(_registration(episode.episode_id, lease))

    receipt = await coordinator.propose(
        "session-1",
        YieldProposal(
            reason="阶段调研已经结束",
            suggested_task_state=YieldSuggestedState.READY,
            waiting_condition=None,
            current_judgment="两份真实 trace 的终态语义一致",
            next_steps=("整理结论",),
            continue_same_task=True,
        ),
    )

    assert receipt.status == "registered"
    assert store.read_snapshot().episode_by_id(episode.episode_id).status == EpisodeStatus.ACTIVE
    assert runtime.releases == []

    result = await coordinator.observe(
        "session-1", _event("finished"), generation="generation-1"
    )

    assert result is not None and result.status == "closed"
    state = store.read_snapshot().episode_by_id(episode.episode_id)
    assert state.status == EpisodeStatus.CLOSED
    assert state.last_snapshot_ref is not None
    saved = store.read_episode_snapshot(state.last_snapshot_ref)
    assert saved.source == SnapshotSource.COOPERATIVE
    assert saved.current_judgment == "两份真实 trace 的终态语义一致"
    assert runtime.interrupts == []
    assert runtime.releases == ["work-lease-1"]


@pytest.mark.anyio
async def test_codex_interrupt_ack_waits_for_native_terminal(
    store: ModelOsStore,
) -> None:
    episode, lease, _ = make_running_system_episode(store)
    activate_episode(store, episode.episode_id, lease)
    runtime = FakeRuntime()
    coordinator = YieldCoordinator(
        store,
        interrupt_runtime=runtime.interrupt,
        release_work_lease=runtime.release,
    )
    await coordinator.register_turn(_registration(episode.episode_id, lease))

    receipt = await coordinator.request_forced(
        "session-1",
        ForceYieldReason.CONTEXT_LIMIT,
        expected_turn_id="turn-1",
        expected_generation="generation-1",
    )

    assert receipt.status == "interrupt_sent"
    assert runtime.interrupts == ["session-1"]
    assert store.read_snapshot().episode_by_id(episode.episode_id).status == EpisodeStatus.ACTIVE
    assert runtime.releases == []

    result = await coordinator.observe(
        "session-1",
        _event("interrupted", payload={"status": "interrupted"}),
        generation="generation-1",
    )

    assert result is not None and result.status == "closed"
    state = store.read_snapshot().episode_by_id(episode.episode_id)
    assert state.status == EpisodeStatus.CLOSED
    assert state.last_snapshot_ref is not None
    assert store.read_episode_snapshot(state.last_snapshot_ref).source == SnapshotSource.RECOVERY_PARTIAL


@pytest.mark.anyio
async def test_unresolved_tool_forced_stop_never_closes_for_fresh(
    store: ModelOsStore,
) -> None:
    episode, lease, _ = make_running_system_episode(store)
    activate_episode(store, episode.episode_id, lease)
    runtime = FakeRuntime()
    coordinator = YieldCoordinator(
        store,
        interrupt_runtime=runtime.interrupt,
        release_work_lease=runtime.release,
    )
    await coordinator.register_turn(
        _registration(episode.episode_id, lease, runtime="claude_code")
    )
    await coordinator.observe(
        "session-1",
        _event(
            "tool_call",
            payload={"tool_use_id": "tool-1", "tool_name": "Write"},
            item_id="tool-1",
        ),
        generation="generation-1",
    )

    receipt = await coordinator.request_forced(
        "session-1",
        ForceYieldReason.RUNTIME_TIMEOUT,
        expected_turn_id="turn-1",
        expected_generation="generation-1",
    )
    assert receipt.status == "interrupt_sent"

    result = await coordinator.observe(
        "session-1",
        _event("error", payload={"subclass": "error_during_execution"}),
        generation="generation-1",
    )

    assert result is not None and result.status == "reconcile_required"
    state = store.read_snapshot().episode_by_id(episode.episode_id)
    assert state.status == EpisodeStatus.RECONCILE_REQUIRED
    assert state.last_snapshot_ref is not None
    snapshot = store.read_episode_snapshot(state.last_snapshot_ref)
    assert snapshot.source == SnapshotSource.RECOVERY_PARTIAL
    assert any(item.outcome == "unknown_requires_reconcile" for item in snapshot.side_effects)
    assert runtime.releases == ["work-lease-1"]


@pytest.mark.anyio
async def test_late_interrupt_after_terminal_is_stale_no_action(
    store: ModelOsStore,
) -> None:
    episode, lease, _ = make_running_system_episode(store)
    activate_episode(store, episode.episode_id, lease)
    runtime = FakeRuntime()
    coordinator = YieldCoordinator(
        store,
        interrupt_runtime=runtime.interrupt,
        release_work_lease=runtime.release,
    )
    await coordinator.register_turn(
        _registration(episode.episode_id, lease, runtime="claude_code")
    )
    assert (
        await coordinator.observe(
            "session-1", _event("finished"), generation="generation-1"
        )
        is None
    )

    receipt = await coordinator.request_forced(
        "session-1",
        ForceYieldReason.USER_PREEMPT,
        expected_turn_id="turn-1",
        expected_generation="generation-1",
    )

    assert receipt.status == "no_action:stale_turn"
    assert runtime.interrupts == []
    assert store.read_snapshot().episode_by_id(episode.episode_id).status == EpisodeStatus.ACTIVE


@pytest.mark.anyio
async def test_normal_finished_ignores_following_session_exit(
    store: ModelOsStore,
) -> None:
    episode, lease, _ = make_running_system_episode(store)
    activate_episode(store, episode.episode_id, lease)
    runtime = FakeRuntime()
    coordinator = YieldCoordinator(
        store,
        interrupt_runtime=runtime.interrupt,
        release_work_lease=runtime.release,
    )
    await coordinator.register_turn(
        _registration(episode.episode_id, lease, runtime="claude_code")
    )
    assert (
        await coordinator.observe(
            "session-1", _event("finished"), generation="generation-1"
        )
        is None
    )
    assert (
        await coordinator.observe(
            "session-1",
            _event("session_exited", payload={"returncode": 0}),
            generation="generation-1",
        )
        is None
    )
    assert store.read_snapshot().episode_by_id(episode.episode_id).status == EpisodeStatus.ACTIVE
    assert runtime.releases == ["work-lease-1"]


@pytest.mark.anyio
async def test_uncorrelated_interrupted_cannot_commit_cooperative_snapshot(
    store: ModelOsStore,
) -> None:
    episode, lease, _ = make_running_system_episode(store)
    activate_episode(store, episode.episode_id, lease)
    runtime = FakeRuntime()
    coordinator = YieldCoordinator(
        store,
        interrupt_runtime=runtime.interrupt,
        release_work_lease=runtime.release,
    )
    await coordinator.register_turn(_registration(episode.episode_id, lease))
    await coordinator.propose(
        "session-1",
        YieldProposal(
            reason="phase done",
            suggested_task_state=YieldSuggestedState.READY,
            waiting_condition=None,
            current_judgment="draft only",
            next_steps=(),
            continue_same_task=True,
        ),
    )

    receipt = await coordinator.observe(
        "session-1", _event("interrupted"), generation="generation-1"
    )

    assert receipt is not None and receipt.status == "reconcile_required"
    state = store.read_snapshot().episode_by_id(episode.episode_id)
    assert state.status == EpisodeStatus.RECONCILE_REQUIRED
    assert state.last_snapshot_ref is not None
    assert store.read_episode_snapshot(state.last_snapshot_ref).source == SnapshotSource.RECOVERY_PARTIAL


@pytest.mark.anyio
async def test_work_lease_releases_before_episode_ownership(
    store: ModelOsStore,
) -> None:
    episode, lease, _ = make_running_system_episode(store)
    activate_episode(store, episode.episode_id, lease)
    statuses: list[EpisodeStatus] = []

    async def release(_work_lease_id: str) -> None:
        current = store.read_snapshot().episode_by_id(episode.episode_id)
        statuses.append(current.status)
        row = store._read_episode_lease_row(episode.episode_id)
        assert row is not None and row["released_at"] is None

    coordinator = YieldCoordinator(
        store,
        interrupt_runtime=FakeRuntime().interrupt,
        release_work_lease=release,
    )
    await coordinator.register_turn(_registration(episode.episode_id, lease))
    await coordinator.propose(
        "session-1",
        YieldProposal(
            reason="phase done",
            suggested_task_state=YieldSuggestedState.READY,
            waiting_condition=None,
            current_judgment="verified",
            next_steps=(),
            continue_same_task=True,
        ),
    )
    await coordinator.observe(
        "session-1", _event("finished"), generation="generation-1"
    )

    assert statuses == [EpisodeStatus.CHECKPOINTING]
    row = store._read_episode_lease_row(episode.episode_id)
    assert row is None or row["released_at"] is not None


@pytest.mark.anyio
async def test_terminal_retry_continues_after_work_lease_release_failure(
    store: ModelOsStore,
) -> None:
    episode, lease, _ = make_running_system_episode(store)
    activate_episode(store, episode.episode_id, lease)
    release_attempts = 0

    async def fail_once(_work_lease_id: str) -> None:
        nonlocal release_attempts
        release_attempts += 1
        if release_attempts == 1:
            raise RuntimeError("broker unavailable")

    runtime = FakeRuntime()
    coordinator = YieldCoordinator(
        store,
        interrupt_runtime=runtime.interrupt,
        release_work_lease=fail_once,
    )
    await coordinator.register_turn(_registration(episode.episode_id, lease))
    await coordinator.propose(
        "session-1",
        YieldProposal(
            reason="phase complete",
            suggested_task_state=YieldSuggestedState.READY,
            waiting_condition=None,
            current_judgment="checkpoint is ready",
            next_steps=("continue later",),
            continue_same_task=True,
        ),
    )

    with pytest.raises(RuntimeError, match="broker unavailable"):
        await coordinator.observe(
            "session-1", _event("finished"), generation="generation-1"
        )
    assert store.read_snapshot().episode_by_id(episode.episode_id).status == EpisodeStatus.CHECKPOINTING

    receipt = await coordinator.observe(
        "session-1", _event("finished"), generation="generation-1"
    )

    assert receipt is not None and receipt.status == "closed"
    assert release_attempts == 2
    assert store.read_snapshot().episode_by_id(episode.episode_id).status == EpisodeStatus.CLOSED


@pytest.mark.anyio
async def test_terminal_retry_continues_after_close_failure(
    store: ModelOsStore, monkeypatch
) -> None:
    episode, lease, _ = make_running_system_episode(store)
    activate_episode(store, episode.episode_id, lease)
    runtime = FakeRuntime()
    coordinator = YieldCoordinator(
        store,
        interrupt_runtime=runtime.interrupt,
        release_work_lease=runtime.release,
    )
    await coordinator.register_turn(_registration(episode.episode_id, lease))
    await coordinator.request_forced(
        "session-1",
        ForceYieldReason.HARD_BUDGET,
        expected_turn_id="turn-1",
        expected_generation="generation-1",
    )
    original_close = store.close_episode
    attempts = 0

    def fail_once(*args, **kwargs) -> None:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("close write failed")
        original_close(*args, **kwargs)

    monkeypatch.setattr(store, "close_episode", fail_once)

    with pytest.raises(RuntimeError, match="close write failed"):
        await coordinator.observe(
            "session-1", _event("finished"), generation="generation-1"
        )
    assert runtime.releases == ["work-lease-1"]

    receipt = await coordinator.observe(
        "session-1", _event("finished"), generation="generation-1"
    )

    assert receipt is not None and receipt.status == "closed"
    assert attempts == 2
    assert runtime.releases == ["work-lease-1"]


@pytest.mark.anyio
async def test_unknown_terminal_retry_finishes_after_reconcile_was_persisted(
    store: ModelOsStore, monkeypatch
) -> None:
    episode, lease, _ = make_running_system_episode(store)
    activate_episode(store, episode.episode_id, lease)
    runtime = FakeRuntime()
    coordinator = YieldCoordinator(
        store,
        interrupt_runtime=runtime.interrupt,
        release_work_lease=runtime.release,
    )
    await coordinator.register_turn(_registration(episode.episode_id, lease))
    original_append = store.append_decision
    attempts = 0

    def fail_boundary_once(decision) -> int:
        nonlocal attempts
        if decision.kind == "yield.boundary":
            attempts += 1
            if attempts == 1:
                raise RuntimeError("terminal audit failed")
        return original_append(decision)

    monkeypatch.setattr(store, "append_decision", fail_boundary_once)

    with pytest.raises(RuntimeError, match="terminal audit failed"):
        await coordinator.observe(
            "session-1",
            _event("error", payload={"subclass": "host_error"}),
            generation="generation-1",
        )
    assert store.read_snapshot().episode_by_id(episode.episode_id).status == EpisodeStatus.RECONCILE_REQUIRED

    receipt = await coordinator.observe(
        "session-1",
        _event("error", payload={"subclass": "host_error"}),
        generation="generation-1",
    )

    assert receipt is not None and receipt.status == "reconcile_required"
    assert attempts == 2
    assert runtime.releases == ["work-lease-1"]


@pytest.mark.anyio
async def test_unknown_interrupt_result_is_not_automatically_replayed(
    store: ModelOsStore,
) -> None:
    episode, lease, _ = make_running_system_episode(store)
    activate_episode(store, episode.episode_id, lease)
    attempts = 0

    async def fail_interrupt(_session_id: str) -> None:
        nonlocal attempts
        attempts += 1
        raise RuntimeError("interrupt outcome unknown")

    coordinator = YieldCoordinator(
        store,
        interrupt_runtime=fail_interrupt,
        release_work_lease=lambda _lease_id: None,
    )
    await coordinator.register_turn(_registration(episode.episode_id, lease))

    receipt = await coordinator.request_forced(
        "session-1",
        ForceYieldReason.RUNTIME_TIMEOUT,
        expected_turn_id="turn-1",
        expected_generation="generation-1",
    )
    assert receipt.status == "interrupt_failed"
    await coordinator.observe(
        "session-1",
        _event(
            "tool_call",
            item_id="tool-1",
            payload={"tool_use_id": "tool-1", "tool_name": "Bash"},
        ),
        generation="generation-1",
    )
    await coordinator.observe(
        "session-1",
        _event(
            "tool_result",
            item_id="tool-1",
            payload={"tool_use_id": "tool-1"},
        ),
        generation="generation-1",
    )

    assert attempts == 1
    terminal = await coordinator.observe(
        "session-1", _event("finished"), generation="generation-1"
    )
    assert terminal is not None and terminal.status == "reconcile_required"
