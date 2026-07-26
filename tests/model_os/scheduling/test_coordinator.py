import pytest

from trowel_py.model_os.scheduling import AttentionScheduler, ScheduleAction
from trowel_py.model_os.store import ModelOsStore, TaskCommandError
from trowel_py.model_os.types import EventKind
from trowel_py.model_os.types import EventEnvelope, Provenance


def _warm(store: ModelOsStore, name: str):
    task = store.create_task_from_user_request(
        original_goal=f"匿名任务 {name}",
        idempotency_key=f"create-{name}",
    )
    store.promote_to_warm(task.task_id)
    return task


class YieldRecorder:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    async def __call__(self, current_task_id: str, target_task_id: str) -> str:
        self.calls.append((current_task_id, target_task_id))
        return "yield_requested"


@pytest.mark.anyio
async def test_user_override_is_durable_and_requests_safe_yield_once(
    store: ModelOsStore,
) -> None:
    current = _warm(store, "current")
    target = _warm(store, "target")
    store.claim_foreground(current.task_id)
    yielding = YieldRecorder()
    scheduler = AttentionScheduler(store, request_user_preempt=yielding)

    first = await scheduler.request_foreground(
        target.task_id,
        idempotency_key="choose-target",
    )
    second = await scheduler.request_foreground(
        target.task_id,
        idempotency_key="choose-target",
    )

    assert first == second
    assert first.decision.action is ScheduleAction.REQUEST_YIELD
    assert first.result_code == "yield_requested"
    assert yielding.calls == [(current.task_id, target.task_id)]
    events = [
        event
        for _, event in store.list_events()
        if event.kind == EventKind.ATTENTION_FOREGROUND_REQUESTED
    ]
    assert len(events) == 1
    assert events[0].source == "user"


@pytest.mark.anyio
async def test_user_override_at_boundary_leaves_retryable_dispatch_intent(
    store: ModelOsStore,
) -> None:
    target = _warm(store, "target")
    scheduler = AttentionScheduler(store, request_user_preempt=YieldRecorder())

    outcome = await scheduler.request_foreground(
        target.task_id,
        idempotency_key="choose-idle-target",
    )

    assert outcome.decision.action is ScheduleAction.DISPATCH
    assert outcome.result_code == "configuration_required"
    assert outcome.recorded.intent_event_id is not None
    assert scheduler.pending_override_task_id() == target.task_id


@pytest.mark.anyio
async def test_non_runnable_override_is_rejected_without_promoting_task(
    store: ModelOsStore,
) -> None:
    backlog = store.create_task_from_user_request(
        original_goal="匿名 backlog",
        idempotency_key="create-backlog",
    )
    scheduler = AttentionScheduler(store, request_user_preempt=YieldRecorder())

    with pytest.raises(TaskCommandError, match="ready warm"):
        await scheduler.request_foreground(
            backlog.task_id,
            idempotency_key="choose-backlog",
        )

    assert store.read_snapshot().tasks[0].warm is False
    assert store.list_decisions() == []


@pytest.mark.anyio
async def test_foreground_idempotency_key_cannot_change_target(
    store: ModelOsStore,
) -> None:
    first = _warm(store, "first")
    second = _warm(store, "second")
    scheduler = AttentionScheduler(store, request_user_preempt=YieldRecorder())
    await scheduler.request_foreground(
        first.task_id,
        idempotency_key="choose-once",
    )

    with pytest.raises(TaskCommandError, match="already bound"):
        await scheduler.request_foreground(
            second.task_id,
            idempotency_key="choose-once",
        )


@pytest.mark.anyio
async def test_override_target_wins_after_current_foreground_releases(
    store: ModelOsStore,
) -> None:
    current = _warm(store, "current-release")
    target = _warm(store, "target-release")
    store.claim_foreground(current.task_id)
    scheduler = AttentionScheduler(store, request_user_preempt=YieldRecorder())
    await scheduler.request_foreground(
        target.task_id,
        idempotency_key="choose-after-release",
    )

    store.release_foreground()
    outcome = await scheduler.trigger("episode.current.closed")

    assert outcome.decision.action is ScheduleAction.DISPATCH
    assert outcome.decision.target_task_id == target.task_id


@pytest.mark.anyio
async def test_selecting_current_task_is_resolved_before_natural_release(
    store: ModelOsStore,
) -> None:
    current = _warm(store, "selected-current")
    peer = _warm(store, "selected-peer")
    store.claim_foreground(current.task_id)
    scheduler = AttentionScheduler(store, request_user_preempt=YieldRecorder())

    selected = await scheduler.request_foreground(
        current.task_id,
        idempotency_key="keep-current",
    )
    store.release_foreground()
    next_outcome = await scheduler.trigger("episode.current.natural-boundary")

    assert selected.result_code == "user_override_current"
    assert scheduler.pending_override_task_id() is None
    assert next_outcome.decision.target_task_id == peer.task_id


@pytest.mark.anyio
async def test_automatic_trigger_does_not_preempt_active_focus(
    store: ModelOsStore,
) -> None:
    current = _warm(store, "current")
    _warm(store, "peer")
    store.claim_foreground(current.task_id)
    yielding = YieldRecorder()
    scheduler = AttentionScheduler(store, request_user_preempt=yielding)

    outcome = await scheduler.trigger("wake.peer.ready")

    assert outcome.decision.action is ScheduleAction.CONTINUE
    assert outcome.result_code == "active_focus"
    assert yielding.calls == []


@pytest.mark.anyio
async def test_out_of_order_old_event_is_recorded_as_stale_trigger(
    store: ModelOsStore,
) -> None:
    for index in (1, 2):
        store.append_event(
            EventEnvelope(
                event_id=f"external.event.{index}",
                kind=EventKind.NOTE,
                occurred_at=f"2026-07-26T00:00:0{index}Z",
                source="test",
                provenance=Provenance.MACHINE_OBSERVATION,
                policy_version="v0",
                payload={},
            )
        )
    scheduler = AttentionScheduler(store, request_user_preempt=YieldRecorder())
    await scheduler.trigger("external.event.2")

    stale = await scheduler.trigger("external.event.1")

    assert stale.result_code == "stale_trigger"
    assert stale.decision.reason.value == "stale_trigger"
