"""直接驱动 reducer，验证 provenance 强度与禁止静默升级的规则。"""

from __future__ import annotations

import pytest

from trowel_py.model_os.reducer import initial_snapshot, reduce_event
from trowel_py.model_os.types import (
    EventEnvelope,
    EventKind,
    MemoryEligibility,
    Provenance,
    SessionPurpose,
    WorkItemKind,
    WorkItemStatus,
)


def _created(*, work_item_id: str = "wi-1") -> EventEnvelope:
    return EventEnvelope(
        event_id=f"create-{work_item_id}",
        kind=EventKind.WORK_ITEM_CREATED,
        occurred_at="2026-07-21T00:00:00Z",
        source="test",
        provenance=Provenance.USER_DECISION,
        policy_version="v0",
        payload={
            "work_item_id": work_item_id,
            "kind": WorkItemKind.TASK.value,
            "owner_ref": "user",
            "task_id": "task-A",
            "status": WorkItemStatus.PENDING.value,
            "session_purpose": SessionPurpose.FOREGROUND.value,
            "memory_eligibility": MemoryEligibility.ELIGIBLE.value,
        },
        work_item_id=work_item_id,
    )


def _status(
    *,
    work_item_id: str,
    event_id: str,
    new_status: WorkItemStatus,
    provenance: Provenance,
) -> EventEnvelope:
    return EventEnvelope(
        event_id=event_id,
        kind=EventKind.WORK_ITEM_STATUS_CHANGED,
        occurred_at="2026-07-21T00:00:01Z",
        source="test",
        provenance=provenance,
        policy_version="v0",
        payload={"new_status": new_status.value},
        work_item_id=work_item_id,
    )


@pytest.mark.parametrize(
    "provenance",
    [
        Provenance.USER_DECISION,
        Provenance.MACHINE_OBSERVATION,
        Provenance.MODEL_HYPOTHESIS,
        Provenance.UNKNOWN,
    ],
)
def test_each_provenance_is_recorded_on_first_claim(provenance: Provenance) -> None:
    snap = reduce_event(initial_snapshot(), _created())
    snap = reduce_event(
        snap,
        _status(
            work_item_id="wi-1",
            event_id="e-status",
            new_status=WorkItemStatus.RUNNING,
            provenance=provenance,
        ),
    )
    assert snap.work_items[0].status == WorkItemStatus.RUNNING
    assert snap.work_items[0].status_provenance == provenance


def test_stale_cannot_assert_status() -> None:
    snap = reduce_event(initial_snapshot(), _created())
    assert snap.work_items[0].status_provenance == Provenance.STALE
    snap = reduce_event(
        snap,
        _status(
            work_item_id="wi-1",
            event_id="e-stale",
            new_status=WorkItemStatus.DONE,
            provenance=Provenance.STALE,
        ),
    )
    assert snap.work_items[0].status == WorkItemStatus.PENDING
    assert snap.work_items[0].status_provenance == Provenance.STALE


@pytest.mark.parametrize(
    "strong,weak",
    [
        (Provenance.USER_DECISION, Provenance.MACHINE_OBSERVATION),
        (Provenance.USER_DECISION, Provenance.MODEL_HYPOTHESIS),
        (Provenance.USER_DECISION, Provenance.UNKNOWN),
        (Provenance.USER_DECISION, Provenance.STALE),
        (Provenance.MACHINE_OBSERVATION, Provenance.MODEL_HYPOTHESIS),
        (Provenance.MACHINE_OBSERVATION, Provenance.UNKNOWN),
        (Provenance.MACHINE_OBSERVATION, Provenance.STALE),
        (Provenance.MODEL_HYPOTHESIS, Provenance.UNKNOWN),
        (Provenance.MODEL_HYPOTHESIS, Provenance.STALE),
        (Provenance.UNKNOWN, Provenance.STALE),
    ],
)
def test_weaker_provenance_cannot_override_stronger(
    strong: Provenance, weak: Provenance
) -> None:
    snap = reduce_event(initial_snapshot(), _created())
    snap = reduce_event(
        snap,
        _status(
            work_item_id="wi-1",
            event_id="e-strong",
            new_status=WorkItemStatus.RUNNING,
            provenance=strong,
        ),
    )
    snap = reduce_event(
        snap,
        _status(
            work_item_id="wi-1",
            event_id="e-weak",
            new_status=WorkItemStatus.DONE,
            provenance=weak,
        ),
    )
    assert snap.work_items[0].status == WorkItemStatus.RUNNING
    assert snap.work_items[0].status_provenance == strong


def test_equal_provenance_can_update_status() -> None:
    snap = reduce_event(initial_snapshot(), _created())
    snap = reduce_event(
        snap,
        _status(
            work_item_id="wi-1",
            event_id="e1",
            new_status=WorkItemStatus.RUNNING,
            provenance=Provenance.MACHINE_OBSERVATION,
        ),
    )
    snap = reduce_event(
        snap,
        _status(
            work_item_id="wi-1",
            event_id="e2",
            new_status=WorkItemStatus.DONE,
            provenance=Provenance.MACHINE_OBSERVATION,
        ),
    )
    assert snap.work_items[0].status == WorkItemStatus.DONE
    assert snap.work_items[0].status_provenance == Provenance.MACHINE_OBSERVATION


def test_stale_event_is_recorded_but_cannot_promote() -> None:
    snap = reduce_event(initial_snapshot(), _created())
    snap = reduce_event(
        snap,
        _status(
            work_item_id="wi-1",
            event_id="e1",
            new_status=WorkItemStatus.RUNNING,
            provenance=Provenance.MACHINE_OBSERVATION,
        ),
    )
    snap = reduce_event(
        snap,
        _status(
            work_item_id="wi-1",
            event_id="e2",
            new_status=WorkItemStatus.CANCELLED,
            provenance=Provenance.STALE,
        ),
    )
    assert snap.work_items[0].status == WorkItemStatus.RUNNING


def test_unknown_event_is_recorded_but_cannot_promote() -> None:
    snap = reduce_event(initial_snapshot(), _created())
    snap = reduce_event(
        snap,
        _status(
            work_item_id="wi-1",
            event_id="e1",
            new_status=WorkItemStatus.RUNNING,
            provenance=Provenance.MACHINE_OBSERVATION,
        ),
    )
    snap = reduce_event(
        snap,
        _status(
            work_item_id="wi-1",
            event_id="e2",
            new_status=WorkItemStatus.DONE,
            provenance=Provenance.UNKNOWN,
        ),
    )
    assert snap.work_items[0].status == WorkItemStatus.RUNNING


def test_provenance_strength_ordering() -> None:
    assert Provenance.USER_DECISION.strength > Provenance.MACHINE_OBSERVATION.strength
    assert (
        Provenance.MACHINE_OBSERVATION.strength > Provenance.MODEL_HYPOTHESIS.strength
    )
    assert Provenance.MODEL_HYPOTHESIS.strength > Provenance.UNKNOWN.strength
    assert Provenance.UNKNOWN.strength > Provenance.STALE.strength
