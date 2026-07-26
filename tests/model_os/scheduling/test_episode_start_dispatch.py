from __future__ import annotations

import pytest

from trowel_py.model_os.episode_starting import (
    NativeSessionIdentity,
    StartEpisodeCommand,
    StartEpisodeCoordinator,
)
from trowel_py.model_os.scheduling import (
    build_schedule_input,
    decide_schedule,
    record_schedule_decision,
)
from trowel_py.model_os.types import MemoryEligibility, SessionPurpose, TaskStatus
from trowel_py.model_os.work_broker import (
    DenialReason,
    WorkDenial,
    WorkLease,
)
from trowel_py.quota.types import Provider


class OrderingBroker:
    def __init__(self, store, *, denial_reason: DenialReason | None = None) -> None:
        self.store = store
        self.denial_reason = denial_reason
        self.episode_seen_before_request = False
        self.started = []

    def request(self, request):
        self.episode_seen_before_request = bool(self.store.read_snapshot().episodes)
        if self.denial_reason is not None:
            return WorkDenial(self.denial_reason, "unavailable")
        return WorkLease(
            lease_id="work-lease-1",
            slot="codex:account:0",
            provider=Provider.CODEX,
            account_id="codex",
            work_kind=request.kind,
            model_tier=request.model_tier,
            granted_cap=None,
            acquired_at="2026-07-26T00:00:00+00:00",
            expires_at="2026-07-26T00:10:00+00:00",
            fencing_token=1,
            task_id=request.task_id,
            work_item_id=request.work_item_id,
        )

    def begin_call(self, lease_id, token):
        self.started.append((lease_id, token))

    def release(self, _lease_id, _token):
        return True


class BusyOnceBroker(OrderingBroker):
    def __init__(self, store) -> None:
        super().__init__(store)
        self.requests = 0

    def request(self, request):
        self.requests += 1
        if self.requests == 1:
            self.episode_seen_before_request = bool(self.store.read_snapshot().episodes)
            return WorkDenial(DenialReason.SLOT_BUSY, "incubation is running")
        return super().request(request)


class Adapter:
    def __init__(self) -> None:
        self.start_calls = 0

    async def start_native(self, _command, _episode):
        self.start_calls += 1
        return NativeSessionIdentity(
            agent_session_id="agent-1",
            runtime="codex",
            native_session_id="thread-1",
            runtime_generation="generation-1",
        )

    async def persist_binding(self, identity):
        return identity

    async def refresh_identity(self, identity):
        return identity

    def effective_settings(self, _identity):
        return "model-1", "high"

    async def start_first_turn(self, _identity, _text):
        yield {
            "type": "turn_start",
            "turn_id": "turn-1",
            "payload": {},
        }
        yield {"type": "finished", "turn_id": "turn-1", "payload": {}}


class Yielding:
    async def register_turn(self, _registration):
        return None


def _task_and_command(store, *, with_decision: bool = True):
    task = store.create_task_from_user_request(
        original_goal="匿名启动任务",
        idempotency_key="create-start-task",
    )
    store.promote_to_warm(task.task_id)
    decision_id = None
    if with_decision:
        recorded = record_schedule_decision(
            store,
            decide_schedule(
                build_schedule_input(store, trigger_event_ref="trigger.start")
            ),
        )
        decision_id = recorded.decision_id
    command = StartEpisodeCommand(
        work_item_id=task.primary_work_item_id,
        task_id=task.task_id,
        previous_episode_id=None,
        previous_snapshot_ref=None,
        runtime="codex",
        model="model-1",
        effort="high",
        memory_enabled=True,
        profile_enabled=True,
        workdir="/workspace/project",
        session_purpose=SessionPurpose.FOREGROUND,
        memory_eligibility=MemoryEligibility.ELIGIBLE,
        permission="danger-full-access",
        idempotency_key="start-task-1",
        schedule_decision_id=decision_id,
    )
    return task, command


async def _collect(coordinator, command):
    return [item async for item in coordinator.start(command)]


@pytest.mark.anyio
async def test_task_start_requires_matching_schedule_dispatch(store) -> None:
    _, command = _task_and_command(store, with_decision=False)
    adapter = Adapter()
    coordinator = StartEpisodeCoordinator(
        store,
        broker=OrderingBroker(store),
        adapter=adapter,
        yield_coordinator=Yielding(),
    )

    with pytest.raises(ValueError, match="schedule decision"):
        await _collect(coordinator, command)
    assert store.read_snapshot().episodes == ()
    assert adapter.start_calls == 0


@pytest.mark.anyio
async def test_ownership_precedes_work_lease_and_foreground_precedes_native(
    store,
) -> None:
    task, command = _task_and_command(store)
    broker = OrderingBroker(store)
    adapter = Adapter()
    coordinator = StartEpisodeCoordinator(
        store,
        broker=broker,
        adapter=adapter,
        yield_coordinator=Yielding(),
    )

    events = await _collect(coordinator, command)

    assert events[-1]["type"] == "finished"
    assert broker.episode_seen_before_request is True
    assert adapter.start_calls == 1
    snapshot = store.read_snapshot()
    assert snapshot.foreground_task_id == task.task_id
    assert (
        next(item for item in snapshot.tasks if item.task_id == task.task_id).status
        is TaskStatus.RUNNING
    )


@pytest.mark.anyio
@pytest.mark.parametrize(
    "denial_reason",
    [DenialReason.SLOT_BUSY, DenialReason.RATE_LIMIT, DenialReason.NO_ACCOUNT],
)
async def test_work_denial_keeps_owned_episode_retryable_without_foreground(
    store,
    denial_reason,
) -> None:
    task, command = _task_and_command(store)
    broker = OrderingBroker(store, denial_reason=denial_reason)
    adapter = Adapter()
    coordinator = StartEpisodeCoordinator(
        store,
        broker=broker,
        adapter=adapter,
        yield_coordinator=Yielding(),
    )

    with pytest.raises(RuntimeError, match="WorkBroker denied"):
        await _collect(coordinator, command)

    snapshot = store.read_snapshot()
    assert broker.episode_seen_before_request is True
    assert len(snapshot.episodes) == 1
    assert snapshot.foreground_task_id is None
    assert adapter.start_calls == 0
    assert (
        next(item for item in snapshot.tasks if item.task_id == task.task_id).status
        is TaskStatus.READY
    )
    deferred = [
        event
        for _, event in store.list_events()
        if event.kind == "attention.resource_deferred"
    ]
    assert deferred[0].payload["reason"] == denial_reason.value


@pytest.mark.anyio
async def test_foreground_retries_after_started_incubation_is_safely_interrupted(
    store,
) -> None:
    task, command = _task_and_command(store)
    broker = BusyOnceBroker(store)
    preempted = []

    async def preempt(provider):
        preempted.append(provider)
        return True

    coordinator = StartEpisodeCoordinator(
        store,
        broker=broker,
        adapter=Adapter(),
        yield_coordinator=Yielding(),
        preempt_started_incubation=preempt,
    )

    events = await _collect(coordinator, command)

    assert events[-1]["type"] == "finished"
    assert preempted == [Provider.CODEX]
    assert broker.requests == 2
    assert store.read_snapshot().foreground_task_id == task.task_id


@pytest.mark.anyio
async def test_resource_retry_reacquires_expired_starting_ownership(store) -> None:
    task, command = _task_and_command(store)
    broker = OrderingBroker(store, denial_reason=DenialReason.SLOT_BUSY)
    adapter = Adapter()
    coordinator = StartEpisodeCoordinator(
        store,
        broker=broker,
        adapter=adapter,
        yield_coordinator=Yielding(),
    )
    with pytest.raises(RuntimeError, match="WorkBroker denied"):
        await _collect(coordinator, command)
    first_episode = store.read_snapshot().episodes[0]
    with store._tx():
        store._conn.execute(
            "UPDATE leases SET expires_at='2000-01-01T00:00:00+00:00' "
            "WHERE resource_type='episode_ownership'"
        )
    broker.denial_reason = None

    events = await _collect(coordinator, command)

    current = store.read_snapshot().episodes
    assert events[-1]["type"] == "finished"
    assert len(current) == 1
    assert current[0].episode_id == first_episode.episode_id
    ownership_events = [
        event
        for _, event in store.list_events()
        if event.kind == "episode.ownership_acquired"
    ]
    assert [event.payload["fencing_token"] for event in ownership_events] == [1, 2]
