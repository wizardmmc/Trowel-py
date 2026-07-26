from __future__ import annotations

import pytest

from tests.model_os._episode_helpers import (
    FakeClock,
    activate_episode,
    make_pending,
    make_running_task_episode,
)
from trowel_py.model_os.scheduling import (
    AttentionScheduler,
    SuspendedEpisodeResumer,
)
from trowel_py.model_os.types import EpisodeStatus, ReconcileReason
from trowel_py.model_os.waking.controller import WakeController
from trowel_py.model_os.work_broker import DenialReason, WorkDenial, WorkLease
from trowel_py.quota.types import Provider


class Broker:
    def __init__(self, store, *, denial_reason: DenialReason | None = None) -> None:
        self.store = store
        self.denial_reason = denial_reason
        self.ownership_seen = False
        self.released = []
        self.requests = []

    def request(self, request):
        self.requests.append(request)
        snapshot = self.store.read_snapshot()
        episode = snapshot.episodes[0]
        self.ownership_seen = any(
            lease.resource_type == "episode_ownership"
            and lease.resource_id == episode.episode_id
            for lease in snapshot.active_leases
        )
        from trowel_py.model_os.work_broker import WorkKind, ModelTier

        assert request.kind is WorkKind.FOREGROUND
        if self.denial_reason is not None:
            return WorkDenial(self.denial_reason, "unavailable")
        return WorkLease(
            lease_id="resume-work-lease",
            slot="codex:account:0",
            provider=Provider.CODEX,
            account_id="codex",
            work_kind=WorkKind.FOREGROUND,
            model_tier=ModelTier.DEEP,
            granted_cap=None,
            acquired_at="2026-07-26T00:00:00+00:00",
            expires_at="2026-07-26T00:10:00+00:00",
            fencing_token=1,
            task_id=request.task_id,
            work_item_id=request.work_item_id,
        )

    def release(self, lease_id, token):
        self.released.append((lease_id, token))
        return True

    def begin_call(self, _lease_id, _token):
        return None


class Runtime:
    def __init__(
        self,
        *,
        fail: bool = False,
        generation: str = "generation-1",
    ) -> None:
        self.fail = fail
        self.generation = generation
        self.answers = []

    def current_generation(self, _binding):
        return self.generation

    async def answer_pending(self, binding, payload):
        self.answers.append((binding, payload))
        if self.fail:
            raise RuntimeError("answer result unknown")
        return "runtime_accepted"


class Yielding:
    def __init__(self) -> None:
        self.resumed = []

    async def resume_suspended_episode(self, **facts):
        self.resumed.append(facts)


async def _no_preempt(_current: str, _target: str) -> str:
    return "yield_requested"


def _suspended(store, monkeypatch):
    clock = FakeClock()
    clock.install(monkeypatch)
    episode, ownership, task, _ = make_running_task_episode(store)
    activate_episode(store, episode.episode_id, ownership)
    store.bind_episode_runtime(
        episode.episode_id,
        expected_lease_id=ownership.lease_id,
        expected_owner=ownership.owner,
        expected_token=ownership.fencing_token,
        agent_session_id="agent-1",
        runtime="codex",
        native_session_id="thread-1",
        runtime_generation="generation-1",
        runtime_pid=None,
        runtime_pgid=None,
        correlation_id="start-1",
        activate=True,
    )
    store.suspend_episode(
        episode.episode_id,
        expected_lease_id=ownership.lease_id,
        expected_owner=ownership.owner,
        expected_token=ownership.fencing_token,
        pending=make_pending(native_generation="generation-1"),
    )
    return episode, task


@pytest.mark.anyio
async def test_suspended_dispatch_resumes_same_episode_and_sends_input_once(
    store, monkeypatch
) -> None:
    episode, task = _suspended(store, monkeypatch)
    wake = WakeController(store)
    wake.queue_for_session(
        "agent-1",
        correlation_id="corr-1",
        runtime_generation="generation-1",
        payload={"request_id": "corr-1", "decision": "accept"},
    )
    broker = Broker(store)
    from trowel_py.model_os.work_broker import ModelTier

    monkeypatch.setattr(
        "trowel_py.model_os.scheduling.resume.route_tier_for_episode",
        lambda _store, _episode_id: ModelTier.FAST,
    )
    runtime = Runtime()
    yielding = Yielding()
    resumer = SuspendedEpisodeResumer(
        store,
        broker=broker,
        wake_controller=wake,
        runtime_adapter=runtime,
        yield_coordinator=yielding,
    )
    scheduler = AttentionScheduler(
        store,
        request_user_preempt=_no_preempt,
        resume_suspended=resumer.resume,
    )

    outcome = await scheduler.trigger("wake.episode.ready")

    assert outcome.result_code == "runtime_accepted"
    assert broker.ownership_seen is True
    assert len(store.read_snapshot().episodes) == 1
    current = store.read_snapshot().episode_by_id(episode.episode_id)
    assert current is not None and current.status is EpisodeStatus.ACTIVE
    assert store.read_snapshot().foreground_task_id == task.task_id
    assert runtime.answers[0][1]["decision"] == "accept"
    assert len(runtime.answers) == 1
    assert len(yielding.resumed) == 1
    assert broker.requests[0].model_tier is ModelTier.FAST
    assert (
        wake.take_pending_input(
            episode.episode_id,
            runtime_generation="generation-1",
        )
        is None
    )


@pytest.mark.anyio
async def test_unknown_pending_send_enters_reconcile_and_is_not_replayed(
    store, monkeypatch
) -> None:
    episode, _ = _suspended(store, monkeypatch)
    wake = WakeController(store)
    wake.queue_for_session(
        "agent-1",
        correlation_id="corr-1",
        runtime_generation="generation-1",
        payload={"request_id": "corr-1", "decision": "accept"},
    )
    runtime = Runtime(fail=True)
    resumer = SuspendedEpisodeResumer(
        store,
        broker=Broker(store),
        wake_controller=wake,
        runtime_adapter=runtime,
        yield_coordinator=Yielding(),
    )
    scheduler = AttentionScheduler(
        store,
        request_user_preempt=_no_preempt,
        resume_suspended=resumer.resume,
    )

    outcome = await scheduler.trigger("wake.episode.unknown")
    repeated = await scheduler.trigger("wake.episode.unknown")

    assert outcome.result_code == "unknown_requires_user_restart"
    assert repeated == outcome
    assert len(runtime.answers) == 1
    current = store.read_snapshot().episode_by_id(episode.episode_id)
    assert current is not None
    assert current.status is EpisodeStatus.RECONCILE_REQUIRED
    assert current.reconcile_reason is ReconcileReason.REQUIRES_USER_RESTART


@pytest.mark.anyio
@pytest.mark.parametrize(
    "denial_reason",
    [DenialReason.SLOT_BUSY, DenialReason.RATE_LIMIT, DenialReason.NO_ACCOUNT],
)
async def test_suspended_resource_denial_stays_retryable(
    store,
    monkeypatch,
    denial_reason,
) -> None:
    episode, _ = _suspended(store, monkeypatch)
    wake = WakeController(store)
    wake.queue_for_session(
        "agent-1",
        correlation_id="corr-1",
        runtime_generation="generation-1",
        payload={"request_id": "corr-1", "decision": "accept"},
    )
    runtime = Runtime()
    resumer = SuspendedEpisodeResumer(
        store,
        broker=Broker(store, denial_reason=denial_reason),
        wake_controller=wake,
        runtime_adapter=runtime,
        yield_coordinator=Yielding(),
    )
    scheduler = AttentionScheduler(
        store,
        request_user_preempt=_no_preempt,
        resume_suspended=resumer.resume,
    )

    outcome = await scheduler.trigger(f"wake.denied.{denial_reason.value}")
    repeated = await scheduler.trigger(f"wake.denied.{denial_reason.value}")

    assert outcome.result_code == f"resource_deferred.{denial_reason.value}"
    assert repeated == outcome
    assert (
        store.read_snapshot().episode_by_id(episode.episode_id).status
        is EpisodeStatus.SUSPENDED_READY
    )
    assert store.read_snapshot().foreground_task_id is None
    assert runtime.answers == []


@pytest.mark.anyio
async def test_resource_retry_continues_original_pending_dispatch(
    store,
    monkeypatch,
) -> None:
    _suspended(store, monkeypatch)
    wake = WakeController(store)
    wake.queue_for_session(
        "agent-1",
        correlation_id="corr-1",
        runtime_generation="generation-1",
        payload={"request_id": "corr-1", "decision": "accept"},
    )
    broker = Broker(store, denial_reason=DenialReason.SLOT_BUSY)
    runtime = Runtime()
    resumer = SuspendedEpisodeResumer(
        store,
        broker=broker,
        wake_controller=wake,
        runtime_adapter=runtime,
        yield_coordinator=Yielding(),
    )
    scheduler = AttentionScheduler(
        store,
        request_user_preempt=_no_preempt,
        resume_suspended=resumer.resume,
    )
    deferred = await scheduler.trigger("wake.resource.deferred")
    broker.denial_reason = None

    retried = await scheduler.trigger("resource.retry.ready")

    assert deferred.result_code == "resource_deferred.slot_busy"
    assert retried.recorded.decision_id == deferred.recorded.decision_id
    assert retried.result_code == "runtime_accepted"
    assert len(store.list_decisions()) == 1
    assert len(runtime.answers) == 1


@pytest.mark.anyio
async def test_live_generation_change_discards_input_without_runtime_answer(
    store,
    monkeypatch,
) -> None:
    episode, _ = _suspended(store, monkeypatch)
    wake = WakeController(store)
    wake.queue_for_session(
        "agent-1",
        correlation_id="corr-1",
        runtime_generation="generation-1",
        payload={"request_id": "corr-1", "decision": "accept"},
    )
    runtime = Runtime(generation="generation-2")
    resumer = SuspendedEpisodeResumer(
        store,
        broker=Broker(store),
        wake_controller=wake,
        runtime_adapter=runtime,
        yield_coordinator=Yielding(),
    )
    scheduler = AttentionScheduler(
        store,
        request_user_preempt=_no_preempt,
        resume_suspended=resumer.resume,
    )

    outcome = await scheduler.trigger("wake.generation.changed")

    assert outcome.result_code == "unknown_requires_user_restart"
    assert runtime.answers == []
    current = store.read_snapshot().episode_by_id(episode.episode_id)
    assert current is not None
    assert current.status is EpisodeStatus.RECONCILE_REQUIRED
