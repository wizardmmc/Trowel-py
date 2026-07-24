from __future__ import annotations

from dataclasses import replace
from typing import Any

import pytest

from trowel_py.model_os import reducer
from trowel_py.model_os.types import EventEnvelope, EventKind, Provenance


def _event(
    kind: str,
    *,
    work_item_id: str | None = "work-1",
    provenance: Provenance = Provenance.MACHINE_OBSERVATION,
    payload: dict[str, Any] | None = None,
) -> EventEnvelope:
    return EventEnvelope(
        event_id="event-work-item-fold-edge",
        kind=kind,
        occurred_at="2026-07-24T00:00:00Z",
        source="test",
        provenance=provenance,
        policy_version="v0",
        payload=payload or {},
        work_item_id=work_item_id,
    )


def _state(work_item_id: str = "work-1"):
    return reducer._work_item_from_created(
        _event(
            EventKind.WORK_ITEM_CREATED,
            work_item_id=work_item_id,
            payload={
                "work_item_id": work_item_id,
                "kind": "task",
                "owner_ref": "task-1",
                "task_id": "task-1",
                "status": "pending",
                "session_purpose": "foreground",
                "memory_eligibility": "eligible",
            },
        )
    )


@pytest.mark.parametrize(
    ("work_item_id", "provenance"),
    [
        (None, Provenance.MACHINE_OBSERVATION),
        ("missing", Provenance.MACHINE_OBSERVATION),
        ("work-1", Provenance.STALE),
    ],
)
def test_status_guards_short_circuit_before_malformed_payload(
    work_item_id: str | None,
    provenance: Provenance,
) -> None:
    snap = replace(reducer.initial_snapshot(), work_items=(_state(),))

    assert (
        reducer._apply_status_change(
            snap,
            _event(
                EventKind.WORK_ITEM_STATUS_CHANGED,
                work_item_id=work_item_id,
                provenance=provenance,
            ),
        )
        is snap
    )


def test_weaker_provenance_short_circuits_before_malformed_payload() -> None:
    strong = replace(_state(), status_provenance=Provenance.USER_DECISION)
    snap = replace(reducer.initial_snapshot(), work_items=(strong,))

    assert (
        reducer._apply_status_change(
            snap,
            _event(
                EventKind.WORK_ITEM_STATUS_CHANGED,
                provenance=Provenance.MACHINE_OBSERVATION,
            ),
        )
        is snap
    )


def test_malformed_duplicate_items_use_first_for_gate_and_replace_all() -> None:
    first = _state()
    other = _state("other")
    duplicate = replace(first, status_provenance=Provenance.USER_DECISION)
    snap = replace(
        reducer.initial_snapshot(),
        work_items=(first, other, duplicate),
    )

    updated = reducer._apply_status_change(
        snap,
        _event(
            EventKind.WORK_ITEM_STATUS_CHANGED,
            provenance=Provenance.MODEL_HYPOTHESIS,
            payload={"new_status": "running"},
        ),
    )

    assert updated.work_items[0] is updated.work_items[2]
    assert updated.work_items[0].status.value == "running"
    assert updated.work_items[1] is other


def test_duplicate_created_short_circuits_before_payload_validation() -> None:
    snap = replace(reducer.initial_snapshot(), work_items=(_state(),))

    assert (
        reducer.reduce_event(
            snap,
            _event(
                EventKind.WORK_ITEM_CREATED,
                payload={"work_item_id": "work-1"},
            ),
        )
        is snap
    )
