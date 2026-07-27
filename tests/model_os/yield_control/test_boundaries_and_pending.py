from __future__ import annotations

import pytest

from trowel_py.model_os.journal import JournalIdentityConflict

from tests.model_os._episode_helpers import (
    activate_episode,
    make_running_task_episode,
)
from trowel_py.model_os.store import ModelOsStore
from trowel_py.model_os.types import EpisodeStatus, WaitingSubtype
from trowel_py.model_os.yielding import (
    ForceYieldReason,
    TurnRegistration,
    YieldCoordinator,
    YieldProposal,
    YieldSuggestedState,
    YieldWaitingCondition,
)


class Runtime:
    def __init__(self) -> None:
        self.interrupts: list[str] = []
        self.releases: list[str] = []

    async def interrupt(self, session_id: str) -> None:
        self.interrupts.append(session_id)

    async def release(self, work_lease_id: str) -> None:
        self.releases.append(work_lease_id)


def _setup(store: ModelOsStore, *, runtime: str = "codex"):
    episode, lease, _, _ = make_running_task_episode(store)
    activate_episode(store, episode.episode_id, lease)
    native = Runtime()
    coordinator = YieldCoordinator(
        store,
        interrupt_runtime=native.interrupt,
        release_work_lease=native.release,
    )
    registration = TurnRegistration(
        session_id="session-1",
        episode_id=episode.episode_id,
        runtime=runtime,
        turn_id="turn-1",
        generation="generation-1",
        native_session_id="native-1",
        ownership_lease_id=lease.lease_id,
        ownership_owner=lease.owner,
        ownership_token=lease.fencing_token,
        work_lease_id="work-lease-1",
    )
    return episode, coordinator, native, registration


def _event(event_type: str, **payload: object) -> dict[str, object]:
    return {
        "type": event_type,
        "turn_id": "turn-1",
        "payload": payload,
    }


@pytest.mark.anyio
@pytest.mark.parametrize(
    "reason",
    [
        ForceYieldReason.CONTEXT_LIMIT,
        ForceYieldReason.HARD_BUDGET,
        ForceYieldReason.USER_PREEMPT,
    ],
)
async def test_normal_force_waits_for_tool_terminal(
    store: ModelOsStore, reason: ForceYieldReason
) -> None:
    episode, coordinator, native, registration = _setup(store)
    await coordinator.register_turn(registration)
    await coordinator.observe(
        "session-1",
        {
            "type": "tool_call",
            "item_id": "tool-1",
            "payload": {"tool_use_id": "tool-1", "tool_name": "Bash"},
        },
        generation="generation-1",
    )

    receipt = await coordinator.request_forced(
        "session-1",
        reason,
        expected_turn_id="turn-1",
        expected_generation="generation-1",
    )
    assert receipt.status == "deferred:unresolved_tool"
    assert native.interrupts == []

    await coordinator.observe(
        "session-1",
        {
            "type": "tool_result",
            "item_id": "tool-1",
            "payload": {"tool_use_id": "tool-1"},
        },
        generation="generation-1",
    )
    assert native.interrupts == ["session-1"]
    assert store.read_snapshot().episode_by_id(episode.episode_id).status == EpisodeStatus.ACTIVE


@pytest.mark.anyio
async def test_subagent_never_auto_cascades_interrupt(store: ModelOsStore) -> None:
    episode, coordinator, native, registration = _setup(store, runtime="claude_code")
    await coordinator.register_turn(registration)
    await coordinator.observe(
        "session-1",
        _event("subagent_progress", task_id="child-1", status="started"),
        generation="generation-1",
    )

    receipt = await coordinator.request_forced(
        "session-1",
        ForceYieldReason.SHUTDOWN,
        expected_turn_id="turn-1",
        expected_generation="generation-1",
    )

    assert receipt.status == "deferred:unresolved_subagent"
    assert native.interrupts == []
    result = await coordinator.observe(
        "session-1",
        _event("session_exited", returncode=-9),
        generation="generation-1",
    )
    assert result is not None and result.status == "reconcile_required"
    assert store.read_snapshot().episode_by_id(episode.episode_id).status == EpisodeStatus.RECONCILE_REQUIRED


@pytest.mark.anyio
async def test_pending_suspends_and_generation_loss_requires_user_restart(
    store: ModelOsStore,
) -> None:
    episode, coordinator, native, registration = _setup(store)
    await coordinator.register_turn(registration)

    receipt = await coordinator.observe(
        "session-1",
        _event("approval_request", request_id="approval-1"),
        generation="generation-1",
    )

    assert receipt is not None and receipt.status == "suspended"
    state = store.read_snapshot().episode_by_id(episode.episode_id)
    assert state.status == EpisodeStatus.SUSPENDED_WAITING_APPROVAL
    assert state.pending_descriptor is not None
    assert state.pending_descriptor.kind == WaitingSubtype.APPROVAL
    assert native.releases == ["work-lease-1"]

    lost = await coordinator.connection_lost(
        "session-1", generation="generation-1"
    )
    assert lost.status == "unknown_requires_user_restart"
    state = store.read_snapshot().episode_by_id(episode.episode_id)
    assert state.status == EpisodeStatus.RECONCILE_REQUIRED
    assert state.pending_descriptor is None
    assert native.releases == ["work-lease-1"]


@pytest.mark.anyio
async def test_answered_approval_event_does_not_suspend_again(
    store: ModelOsStore,
) -> None:
    episode, coordinator, native, registration = _setup(store)
    await coordinator.register_turn(registration)

    receipt = await coordinator.observe(
        "session-1",
        _event(
            "approval_request",
            request_id="approval-1",
            status="answered",
            decision="accept",
        ),
        generation="generation-1",
    )

    assert receipt is None
    assert (
        store.read_snapshot().episode_by_id(episode.episode_id).status
        == EpisodeStatus.ACTIVE
    )
    assert native.releases == []


@pytest.mark.anyio
async def test_pending_retry_releases_work_lease_after_suspend_was_persisted(
    store: ModelOsStore,
) -> None:
    episode, _, native, registration = _setup(store)
    release_attempts = 0

    async def fail_once(work_lease_id: str) -> None:
        nonlocal release_attempts
        release_attempts += 1
        if release_attempts == 1:
            raise RuntimeError("broker unavailable")
        await native.release(work_lease_id)

    coordinator = YieldCoordinator(
        store,
        interrupt_runtime=native.interrupt,
        release_work_lease=fail_once,
    )
    await coordinator.register_turn(registration)
    pending = _event("approval_request", request_id="approval-1")

    with pytest.raises(RuntimeError, match="broker unavailable"):
        await coordinator.observe(
            "session-1", pending, generation="generation-1"
        )
    assert (
        store.read_snapshot().episode_by_id(episode.episode_id).status
        == EpisodeStatus.SUSPENDED_WAITING_APPROVAL
    )
    with pytest.raises(RuntimeError, match="changed request identity"):
        await coordinator.observe(
            "session-1",
            _event("approval_request", request_id="approval-2"),
            generation="generation-1",
        )

    receipt = await coordinator.observe(
        "session-1", pending, generation="generation-1"
    )

    assert receipt is not None and receipt.status == "suspended"
    assert release_attempts == 2
    assert native.releases == ["work-lease-1"]


@pytest.mark.anyio
async def test_repeated_proposal_has_one_checkpoint_owner(store: ModelOsStore) -> None:
    episode, coordinator, _, registration = _setup(store)
    await coordinator.register_turn(registration)
    proposal = YieldProposal(
        reason="phase_done",
        suggested_task_state=YieldSuggestedState.READY,
        waiting_condition=None,
        current_judgment="done",
        next_steps=(),
        continue_same_task=True,
    )
    assert (await coordinator.propose("session-1", proposal)).status == "registered"
    assert (await coordinator.propose("session-1", proposal)).status == "already_registered"

    first = await coordinator.observe(
        "session-1", _event("finished"), generation="generation-1"
    )
    second = await coordinator.observe(
        "session-1", _event("finished"), generation="generation-1"
    )

    assert first == second
    count = store._conn.execute(
        "SELECT COUNT(*) AS n FROM episode_snapshots WHERE episode_id=?",
        (episode.episode_id,),
    ).fetchone()["n"]
    assert count == 1


@pytest.mark.anyio
async def test_restart_cannot_replace_durable_proposal_owner(
    store: ModelOsStore,
) -> None:
    _, coordinator, native, registration = _setup(store)
    await coordinator.register_turn(registration)
    first = YieldProposal(
        reason="phase_done",
        suggested_task_state=YieldSuggestedState.READY,
        waiting_condition=None,
        current_judgment="first judgment",
        next_steps=(),
        continue_same_task=True,
    )
    await coordinator.propose("session-1", first)

    restarted = YieldCoordinator(
        store,
        interrupt_runtime=native.interrupt,
        release_work_lease=native.release,
    )
    await restarted.register_turn(registration)
    with pytest.raises(JournalIdentityConflict):
        await restarted.propose(
            "session-1",
            YieldProposal(
                reason="phase_done",
                suggested_task_state=YieldSuggestedState.READY,
                waiting_condition=None,
                current_judgment="replacement judgment",
                next_steps=(),
                continue_same_task=True,
            ),
        )


@pytest.mark.anyio
async def test_host_loss_without_force_is_partial_unknown(store: ModelOsStore) -> None:
    episode, coordinator, native, registration = _setup(store, runtime="claude_code")
    await coordinator.register_turn(registration)

    receipt = await coordinator.observe(
        "session-1",
        _event("error", subclass="host_error"),
        generation="generation-1",
    )

    assert receipt is not None and receipt.status == "reconcile_required"
    assert store.read_snapshot().episode_by_id(episode.episode_id).status == EpisodeStatus.RECONCILE_REQUIRED
    assert native.releases == ["work-lease-1"]


@pytest.mark.anyio
async def test_only_verifiable_waiting_condition_enters_waiting_event(
    store: ModelOsStore,
) -> None:
    episode, coordinator, _, registration = _setup(store)
    await coordinator.register_turn(registration)
    await coordinator.propose(
        "session-1",
        YieldProposal(
            reason="build still running",
            suggested_task_state=YieldSuggestedState.WAITING_EVENT,
            waiting_condition=YieldWaitingCondition(
                cause="wait for build marker",
                condition_kind="file_exists",
                target_ref="artifact.build.done",
            ),
            current_judgment="build process accepted the job",
            next_steps=("read build result",),
            continue_same_task=True,
        ),
    )
    await coordinator.observe(
        "session-1", _event("finished"), generation="generation-1"
    )

    task = store.read_snapshot().tasks[0]
    assert task.status.value == "waiting_event"
    assert task.waiting_condition is not None
    assert task.waiting_condition.condition_kind == "file_exists"


@pytest.mark.anyio
async def test_unverified_blocked_proposal_returns_task_to_ready(
    store: ModelOsStore,
) -> None:
    _, coordinator, _, registration = _setup(store)
    await coordinator.register_turn(registration)
    await coordinator.propose(
        "session-1",
        YieldProposal(
            reason="I feel blocked",
            suggested_task_state=YieldSuggestedState.WAITING_USER,
            waiting_condition=None,
            current_judgment="unknown",
            next_steps=("inspect the failure",),
            continue_same_task=True,
        ),
    )
    await coordinator.observe(
        "session-1", _event("finished"), generation="generation-1"
    )

    task = store.read_snapshot().tasks[0]
    assert task.status.value == "ready"
    assert task.waiting_condition is None


@pytest.mark.anyio
async def test_user_preempt_does_not_cut_streaming_output(store: ModelOsStore) -> None:
    episode, coordinator, native, registration = _setup(store)
    await coordinator.register_turn(registration)
    await coordinator.observe(
        "session-1",
        _event("text", text="partial reply"),
        generation="generation-1",
    )

    receipt = await coordinator.request_forced(
        "session-1",
        ForceYieldReason.USER_PREEMPT,
        expected_turn_id="turn-1",
        expected_generation="generation-1",
    )
    assert receipt.status == "deferred:stream_in_flight"
    assert native.interrupts == []

    closed = await coordinator.observe(
        "session-1", _event("finished"), generation="generation-1"
    )
    assert closed is not None and closed.status == "closed"
    assert store.read_snapshot().episode_by_id(episode.episode_id).status == EpisodeStatus.CLOSED


@pytest.mark.anyio
async def test_boundary_delay_starts_at_yield_signal(store: ModelOsStore) -> None:
    episode, lease, _, _ = make_running_task_episode(store)
    activate_episode(store, episode.episode_id, lease)
    native = Runtime()
    moments = iter((10.0, 20.0, 25.0))
    coordinator = YieldCoordinator(
        store,
        interrupt_runtime=native.interrupt,
        release_work_lease=native.release,
        monotonic=lambda: next(moments),
    )
    registration = TurnRegistration(
        session_id="session-1",
        episode_id=episode.episode_id,
        runtime="codex",
        turn_id="turn-1",
        generation="generation-1",
        native_session_id="native-1",
        ownership_lease_id=lease.lease_id,
        ownership_owner=lease.owner,
        ownership_token=lease.fencing_token,
        work_lease_id="work-lease-1",
    )
    await coordinator.register_turn(registration)
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

    boundary = next(
        decision
        for _, decision in store.list_decisions()
        if decision.kind == "yield.boundary"
    )
    assert boundary.budget_before == {"delay_ms": 5000}


@pytest.mark.anyio
async def test_waiting_settlement_is_idempotent_after_boundary_audit_failure(
    store: ModelOsStore, monkeypatch
) -> None:
    _, coordinator, native, registration = _setup(store)
    await coordinator.register_turn(registration)
    await coordinator.propose(
        "session-1",
        YieldProposal(
            reason="build is running elsewhere",
            suggested_task_state=YieldSuggestedState.WAITING_EVENT,
            waiting_condition=YieldWaitingCondition(
                cause="wait for build marker",
                condition_kind="file_exists",
                target_ref="artifact.build.done",
            ),
            current_judgment="the external build accepted the job",
            next_steps=("read build result",),
            continue_same_task=True,
        ),
    )
    original_append = store.append_decision
    attempts = 0

    def fail_boundary_once(decision) -> int:
        nonlocal attempts
        if decision.kind == "yield.boundary":
            attempts += 1
            result = original_append(decision)
            if attempts == 1:
                raise RuntimeError("boundary audit failed")
            return result
        return original_append(decision)

    monkeypatch.setattr(store, "append_decision", fail_boundary_once)

    with pytest.raises(RuntimeError, match="boundary audit failed"):
        await coordinator.observe(
            "session-1", _event("finished"), generation="generation-1"
        )
    first = store.read_snapshot().tasks[0]
    assert first.status.value == "waiting_event"

    receipt = await coordinator.observe(
        "session-1", _event("session_exited"), generation="generation-1"
    )

    assert receipt is not None and receipt.status == "closed"
    assert native.releases == ["work-lease-1"]
    waiting_events = [
        event
        for _, event in store.list_events()
        if event.kind == "task.waiting_set"
    ]
    assert len(waiting_events) == 1
    boundary_decisions = [
        decision
        for _, decision in store.list_decisions()
        if decision.kind == "yield.boundary"
    ]
    assert len(boundary_decisions) == 1
