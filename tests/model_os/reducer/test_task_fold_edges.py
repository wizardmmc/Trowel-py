from __future__ import annotations

from dataclasses import replace
from typing import Any

import pytest

from trowel_py.model_os import reducer
from trowel_py.model_os.types import EventEnvelope, EventKind, Provenance

_APPLY_CASES = [
    ("_apply_task_status_change", EventKind.TASK_STATUS_CHANGED),
    ("_apply_task_constraint_appended", EventKind.TASK_CONSTRAINT_APPENDED),
    ("_apply_task_warm_changed", EventKind.TASK_WARM_CHANGED),
    ("_apply_task_warm_rank_set", EventKind.TASK_WARM_RANK_SET),
    ("_apply_task_waiting_set", EventKind.TASK_WAITING_SET),
    ("_apply_task_waiting_cleared", EventKind.TASK_WAITING_CLEARED),
    ("_apply_task_authorization_changed", EventKind.TASK_AUTHORIZATION_CHANGED),
    ("_apply_task_completed", EventKind.TASK_COMPLETED),
    ("_apply_task_cancelled", EventKind.TASK_CANCELLED),
    ("_apply_task_error_recorded", EventKind.TASK_ERROR_RECORDED),
]


def _event(
    kind: str,
    *,
    task_id: str | None = "task-1",
    payload: dict[str, Any] | None = None,
) -> EventEnvelope:
    return EventEnvelope(
        event_id="event-task-fold-edge",
        kind=kind,
        occurred_at="2026-07-24T00:00:00Z",
        source="test",
        provenance=Provenance.MACHINE_OBSERVATION,
        policy_version="v0",
        payload=payload or {},
        task_id=task_id,
    )


def _task(task_id: str = "task-1"):
    return reducer._task_from_created(
        _event(
            EventKind.TASK_CREATED,
            payload={
                "task_id": task_id,
                "origin": "user_request",
                "original_goal": "goal",
            },
        )
    )


@pytest.mark.parametrize(("facade_name", "kind"), _APPLY_CASES)
def test_unknown_task_short_circuits_before_malformed_payload(
    facade_name,
    kind,
) -> None:
    snap = replace(reducer.initial_snapshot(), tasks=(_task("other"),))
    event = _event(kind, task_id="missing", payload={})

    assert getattr(reducer, facade_name)(snap, event) is snap


def test_duplicate_constraint_is_identity_preserving() -> None:
    task = replace(
        _task(),
        appended_constraints=("keep",),
        updated_at="before",
    )
    snap = replace(reducer.initial_snapshot(), tasks=(task,))
    event = _event(
        EventKind.TASK_CONSTRAINT_APPENDED,
        payload={"constraint": "keep"},
    )

    assert reducer._apply_task_constraint_appended(snap, event) is snap
    assert snap.tasks[0].updated_at == "before"


def test_malformed_duplicate_tasks_keep_first_find_and_replace_all() -> None:
    first = _task()
    other = replace(_task("other"), original_goal="other")
    duplicate = replace(first, original_goal="duplicate")
    snap = replace(
        reducer.initial_snapshot(),
        tasks=(first, other, duplicate),
    )
    new_state = replace(first, priority=9)

    assert reducer._find_task(snap, "task-1") is first
    updated = reducer._replace_task(snap, "task-1", new_state)
    assert updated.tasks == (new_state, other, new_state)
    assert updated.tasks[0] is updated.tasks[2]
    assert reducer._replace_task(snap, None, new_state) is snap


def test_duplicate_created_short_circuits_before_payload_validation() -> None:
    snap = replace(reducer.initial_snapshot(), tasks=(_task(),))
    malformed_duplicate = _event(
        EventKind.TASK_CREATED,
        payload={"task_id": "task-1"},
    )

    assert reducer.reduce_event(snap, malformed_duplicate) is snap


def test_unknown_waiting_subtype_falls_back_to_none() -> None:
    waiting = reducer._waiting_from_payload(
        {
            "kind": "waiting_user",
            "subtype": "future-subtype",
        }
    )

    assert waiting.subtype is None


def test_waiting_subtype_type_error_is_not_swallowed() -> None:
    class BadSubtype:
        def __eq__(self, other):
            raise TypeError("bad subtype equality")

    with pytest.raises(TypeError, match="bad subtype equality"):
        reducer._waiting_from_payload(
            {
                "kind": "waiting_user",
                "subtype": BadSubtype(),
            }
        )
