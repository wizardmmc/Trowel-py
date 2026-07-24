from __future__ import annotations

import inspect
from typing import Any

import pytest

from trowel_py.model_os import reducer, task_fold
from trowel_py.model_os.types import EventEnvelope, EventKind, Provenance

_APPLY_CASES = [
    (
        "_apply_task_status_change",
        "apply_task_status_change",
        EventKind.TASK_STATUS_CHANGED,
        False,
    ),
    (
        "_apply_task_constraint_appended",
        "apply_task_constraint_appended",
        EventKind.TASK_CONSTRAINT_APPENDED,
        False,
    ),
    (
        "_apply_task_warm_changed",
        "apply_task_warm_changed",
        EventKind.TASK_WARM_CHANGED,
        False,
    ),
    (
        "_apply_task_warm_rank_set",
        "apply_task_warm_rank_set",
        EventKind.TASK_WARM_RANK_SET,
        False,
    ),
    (
        "_apply_task_waiting_set",
        "apply_task_waiting_set",
        EventKind.TASK_WAITING_SET,
        True,
    ),
    (
        "_apply_task_waiting_cleared",
        "apply_task_waiting_cleared",
        EventKind.TASK_WAITING_CLEARED,
        False,
    ),
    (
        "_apply_task_authorization_changed",
        "apply_task_authorization_changed",
        EventKind.TASK_AUTHORIZATION_CHANGED,
        False,
    ),
    (
        "_apply_task_completed",
        "apply_task_completed",
        EventKind.TASK_COMPLETED,
        False,
    ),
    (
        "_apply_task_cancelled",
        "apply_task_cancelled",
        EventKind.TASK_CANCELLED,
        False,
    ),
    (
        "_apply_task_error_recorded",
        "apply_task_error_recorded",
        EventKind.TASK_ERROR_RECORDED,
        False,
    ),
]


def _event(kind: str, payload: dict[str, Any] | None = None) -> EventEnvelope:
    return EventEnvelope(
        event_id="event-task-fold",
        kind=kind,
        occurred_at="2026-07-24T00:00:00Z",
        source="test",
        provenance=Provenance.MACHINE_OBSERVATION,
        policy_version="v0",
        payload=payload or {},
        task_id="task-1",
    )


def test_task_fold_facades_keep_complete_contracts() -> None:
    expected = {
        "_task_from_created": "(event: 'EventEnvelope') -> 'TaskState'",
        "_find_task": (
            "(snap: 'Snapshot', task_id: 'str | None') -> 'TaskState | None'"
        ),
        "_replace_task": (
            "(snap: 'Snapshot', task_id: 'str | None', "
            "new_state: 'TaskState') -> 'Snapshot'"
        ),
        "_waiting_from_payload": ("(p: 'dict[str, Any]') -> 'WaitingCondition'"),
        **{
            facade: "(snap: 'Snapshot', event: 'EventEnvelope') -> 'Snapshot'"
            for facade, _, _, _ in _APPLY_CASES
        },
    }

    for name, signature in expected.items():
        facade = getattr(reducer, name)
        implementation = (
            task_fold.task_from_created
            if name == "_task_from_created"
            else getattr(task_fold, name)
        )
        assert str(inspect.signature(facade)) == signature
        assert facade.__defaults__ is None
        assert facade.__kwdefaults__ is None
        if name == "_task_from_created":
            assert facade.__module__ == reducer.__name__
            assert facade.__qualname__ == name
            assert facade is not implementation
        else:
            assert facade is implementation
            assert facade.__module__ == task_fold.__name__
            assert facade.__qualname__ == name


def test_created_facade_delegates_current_task_state(monkeypatch) -> None:
    captured: dict[str, Any] = {}
    result = object()

    def implementation(*args, **kwargs):
        captured.update(args=args, kwargs=kwargs)
        return result

    event = _event(EventKind.TASK_CREATED)
    task_state_factory = object()
    monkeypatch.setattr(reducer, "TaskState", task_state_factory)
    monkeypatch.setattr(reducer, "_run_task_from_created", implementation)

    assert reducer._task_from_created(event) is result
    assert captured == {
        "args": (event,),
        "kwargs": {"task_state_factory": task_state_factory},
    }


@pytest.mark.parametrize(
    ("facade_name", "_implementation_name", "kind", "_uses_waiting"),
    _APPLY_CASES,
)
def test_reduce_event_uses_current_apply_facade(
    monkeypatch,
    facade_name,
    _implementation_name,
    kind,
    _uses_waiting,
) -> None:
    marker = object()
    monkeypatch.setattr(reducer, facade_name, lambda snap, event: marker)

    assert reducer.reduce_event(reducer.initial_snapshot(), _event(kind)) is marker


def test_reduce_event_uses_current_created_facade(monkeypatch) -> None:
    marker = object()
    monkeypatch.setattr(reducer, "_task_from_created", lambda event: marker)
    snap = reducer.reduce_event(
        reducer.initial_snapshot(),
        _event(EventKind.TASK_CREATED, {"task_id": "task-1"}),
    )

    assert snap.tasks == (marker,)
