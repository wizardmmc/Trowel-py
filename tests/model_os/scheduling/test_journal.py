from trowel_py.model_os.journal import JournalBoundary
from trowel_py.model_os.scheduling import (
    ScheduleCandidate,
    ScheduleInput,
    decide_schedule,
    read_recorded_schedule,
    record_schedule_decision,
)
from trowel_py.model_os.store import ModelOsStore
from trowel_py.model_os.types import DecisionDisposition, EventKind


def _input(*, candidates: tuple[ScheduleCandidate, ...]) -> ScheduleInput:
    return ScheduleInput(
        trigger_event_ref="wake.event.1",
        journal_boundary=JournalBoundary(event_seq=7, decision_seq=3),
        candidates=candidates,
    )


def _candidate() -> ScheduleCandidate:
    return ScheduleCandidate(
        work_item_id="work-1",
        task_id="task-1",
        priority=0,
        warm_rank=0,
        created_at="2026-07-26T00:00:00Z",
        ready_epoch_ref="task.ready.1",
        virtual_service_segments=0,
    )


def test_dispatch_decision_and_intent_are_atomic_and_idempotent(
    store: ModelOsStore,
) -> None:
    decision = decide_schedule(_input(candidates=(_candidate(),)))

    first = record_schedule_decision(store, decision)
    second = record_schedule_decision(store, decision)

    assert first == second
    decisions = [item for _, item in store.list_decisions()]
    intents = [
        item for _, item in store.list_events() if item.kind == EventKind.COMMAND_INTENT
    ]
    assert len(decisions) == len(intents) == 1
    assert decisions[0].disposition is DecisionDisposition.EXECUTE
    assert intents[0].cause_id == decisions[0].decision_id
    assert read_recorded_schedule(store, first.decision_id) == first


def test_idle_decision_has_no_command_intent(store: ModelOsStore) -> None:
    decision = decide_schedule(_input(candidates=()))

    recorded = record_schedule_decision(store, decision)

    assert recorded.intent_event_id is None
    assert store.list_events() == []
    assert store.list_decisions()[0][1].disposition is DecisionDisposition.NO_ACTION


def test_attention_audit_events_are_known_reducer_noops(store: ModelOsStore) -> None:
    decision = decide_schedule(_input(candidates=(_candidate(),)))
    recorded = record_schedule_decision(store, decision)
    from trowel_py.model_os.scheduling import record_resource_deferred

    record_resource_deferred(store, recorded, reason="slot_busy")

    assert store.read_snapshot().unrecognized_event_kinds == ()
