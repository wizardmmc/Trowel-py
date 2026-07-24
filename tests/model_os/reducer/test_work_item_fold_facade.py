from __future__ import annotations

import inspect
import pickle
import typing
from dataclasses import replace
from typing import Any

from trowel_py import model_os
from trowel_py.model_os import reducer, work_item_fold
from trowel_py.model_os.types import (
    EventEnvelope,
    EventKind,
    Provenance,
)


def _event(kind: str, payload: dict[str, Any] | None = None) -> EventEnvelope:
    return EventEnvelope(
        event_id="event-work-item-fold",
        kind=kind,
        occurred_at="2026-07-24T00:00:00Z",
        source="test",
        provenance=Provenance.MACHINE_OBSERVATION,
        policy_version="v0",
        payload=payload or {},
        work_item_id="work-1",
    )


def _created_payload() -> dict[str, Any]:
    return {
        "work_item_id": "work-1",
        "kind": "task",
        "owner_ref": "task-1",
        "task_id": "task-1",
        "status": "pending",
        "session_purpose": "foreground",
        "memory_eligibility": "eligible",
    }


def test_work_item_fold_facades_keep_complete_contracts() -> None:
    expected = {
        "_work_item_from_created": "(event: 'EventEnvelope') -> 'WorkItemState'",
        "_replace_work_item": (
            "(snap: 'Snapshot', work_item_id: 'str', "
            "new_state: 'WorkItemState') -> 'Snapshot'"
        ),
        "_apply_status_change": (
            "(snap: 'Snapshot', event: 'EventEnvelope') -> 'Snapshot'"
        ),
    }

    for name, signature in expected.items():
        facade = getattr(reducer, name)
        implementation = getattr(work_item_fold, name)
        assert str(inspect.signature(facade)) == signature
        assert facade.__defaults__ is None
        assert facade.__kwdefaults__ is None
        assert facade.__module__ == reducer.__name__
        assert facade.__qualname__ == name
        assert facade is not implementation
        typing.get_type_hints(facade)


def test_work_item_state_keeps_public_identity_and_pickle_path() -> None:
    state = reducer._work_item_from_created(
        _event(EventKind.WORK_ITEM_CREATED, _created_payload())
    )

    assert model_os.WorkItemState is reducer.WorkItemState
    assert reducer.WorkItemState.__module__ == reducer.__name__
    assert pickle.loads(pickle.dumps(reducer.WorkItemState)) is reducer.WorkItemState
    assert pickle.loads(pickle.dumps(state)) == state


def test_created_facade_delegates_current_reducer_dependencies(monkeypatch) -> None:
    captured: dict[str, Any] = {}
    result = object()

    def implementation(*args, **kwargs):
        captured.update(args=args, kwargs=kwargs)
        return result

    event = _event(EventKind.WORK_ITEM_CREATED)
    state_factory = object()
    monkeypatch.setattr(reducer, "WorkItemState", state_factory)
    monkeypatch.setattr(reducer, "_run_work_item_from_created", implementation)

    assert reducer._work_item_from_created(event) is result
    assert captured == {
        "args": (event,),
        "kwargs": {
            "work_item_state_factory": state_factory,
            "work_item_kind": reducer.WorkItemKind,
            "work_item_status": reducer.WorkItemStatus,
            "provenance": reducer.Provenance,
            "session_purpose": reducer.SessionPurpose,
            "memory_eligibility": reducer.MemoryEligibility,
        },
    }


def test_reduce_event_uses_current_work_item_facades(monkeypatch) -> None:
    created = object()
    monkeypatch.setattr(reducer, "_work_item_from_created", lambda event: created)
    snap = reducer.reduce_event(
        reducer.initial_snapshot(),
        _event(EventKind.WORK_ITEM_CREATED, {"work_item_id": "work-1"}),
    )
    assert snap.work_items == (created,)

    changed = object()
    monkeypatch.setattr(reducer, "_apply_status_change", lambda snap, event: changed)
    assert (
        reducer.reduce_event(
            reducer.initial_snapshot(),
            _event(EventKind.WORK_ITEM_STATUS_CHANGED),
        )
        is changed
    )


def test_status_fold_resolves_replace_work_item_at_call_time(monkeypatch) -> None:
    state = reducer._work_item_from_created(
        _event(EventKind.WORK_ITEM_CREATED, _created_payload())
    )
    snap = replace(reducer.initial_snapshot(), work_items=(state,))
    marker = object()
    monkeypatch.setattr(
        reducer,
        "_replace_work_item",
        lambda snap, work_item_id, new_state: marker,
    )

    assert (
        reducer._apply_status_change(
            snap,
            _event(EventKind.WORK_ITEM_STATUS_CHANGED, {"new_status": "running"}),
        )
        is marker
    )


def test_status_fold_resolves_dataclass_replace_at_call_time(monkeypatch) -> None:
    state = reducer._work_item_from_created(
        _event(EventKind.WORK_ITEM_CREATED, _created_payload())
    )
    snap = replace(reducer.initial_snapshot(), work_items=(state,))
    replaced_types: list[type] = []

    def replace_spy(instance, **changes):
        replaced_types.append(type(instance))
        return replace(instance, **changes)

    monkeypatch.setattr(reducer, "replace", replace_spy)
    reducer._apply_status_change(
        snap,
        _event(EventKind.WORK_ITEM_STATUS_CHANGED, {"new_status": "running"}),
    )

    assert replaced_types == [reducer.WorkItemState, reducer.Snapshot]
