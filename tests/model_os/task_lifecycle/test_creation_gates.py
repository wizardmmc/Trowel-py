from trowel_py.model_os.store import ModelOsStore
from trowel_py.model_os.types import EventEnvelope, EventKind, Provenance


def test_entry_point_records_user_decision(store: ModelOsStore) -> None:
    store.create_task_from_user_request(
        original_goal="整理资料",
        idempotency_key="user-request",
        authorization_scope="default",
    )

    created = next(
        event
        for _, event in store.list_events()
        if event.kind == EventKind.TASK_CREATED
    )
    assert created.provenance == Provenance.USER_DECISION


def test_creation_denied_event_is_audit_only(store: ModelOsStore) -> None:
    denied = EventEnvelope(
        event_id="creation-denied",
        kind=EventKind.TASK_CREATION_DENIED,
        occurred_at="2026-07-21T00:00:00Z",
        source="kernel",
        provenance=Provenance.MODEL_HYPOTHESIS,
        policy_version="v0",
        payload={"reason": "model cannot create task directly"},
    )
    store.append_event(denied)

    events = [event for _, event in store.list_events()]
    assert denied in events
    snapshot = store.read_snapshot()
    assert snapshot.tasks == ()
    assert EventKind.TASK_CREATION_DENIED not in (snapshot.unrecognized_event_kinds)


def test_created_payload_contains_only_task_fields(store: ModelOsStore) -> None:
    store.create_task_from_user_request(
        original_goal="整理项目文档",
        idempotency_key="payload-task",
        authorization_scope="default",
    )

    created = next(
        event
        for _, event in store.list_events()
        if event.kind == EventKind.TASK_CREATED
    )
    assert created.payload["original_goal"] == "整理项目文档"
    assert set(created.payload) == {
        "appended_constraints",
        "authorization_scope",
        "origin",
        "original_goal",
        "primary_work_item_id",
        "priority",
        "status",
        "task_id",
        "warm",
        "warm_rank",
    }
