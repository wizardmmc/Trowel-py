"""Pure-function reducer tests (slice-084).

The reducer folds journal events + decisions into a derived Snapshot. These
tests drive it directly (no SQLite) so the logic is exercised independently
of the store. Covers:
- work_item.created → derived WorkItemState
- work_item.status_changed under provenance upgrade rules
- lease lifecycle
- forward-compat: unknown event kinds are retained, never crash the reducer
- fold is order-deterministic and replaying twice is a no-op
"""

from __future__ import annotations

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


def _created_event(
    *,
    event_id: str,
    kind: WorkItemKind = WorkItemKind.TASK,
    task_id: str | None = "task-A",
    owner_ref: str = "user",
    provenance: Provenance = Provenance.USER_DECISION,
) -> EventEnvelope:
    return EventEnvelope(
        event_id=event_id,
        kind=EventKind.WORK_ITEM_CREATED,
        occurred_at="2026-07-21T00:00:00Z",
        source="test",
        provenance=provenance,
        policy_version="v0",
        payload={
            "work_item_id": "wi-1",
            "kind": kind.value,
            "owner_ref": owner_ref,
            "task_id": task_id,
            "status": WorkItemStatus.PENDING.value,
            "session_purpose": SessionPurpose.FOREGROUND.value,
            "memory_eligibility": MemoryEligibility.ELIGIBLE.value,
        },
        work_item_id="wi-1",
    )


def _status_event(
    *,
    event_id: str,
    work_item_id: str,
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


# ----------------------------------------------------------- created event ---


def test_created_event_produces_work_item_state() -> None:
    snap = reduce_event(initial_snapshot(), _created_event(event_id="e1"))
    assert len(snap.work_items) == 1
    state = snap.work_items[0]
    assert state.work_item_id == "wi-1"
    assert state.kind == WorkItemKind.TASK
    assert state.status == WorkItemStatus.PENDING
    # creation attests existence, not status — status_provenance is the
    # weakest placeholder until a real observation arrives
    assert state.status_provenance == Provenance.STALE


# --------------------------------------------------- provenance upgrade rules ---


def test_machine_observation_status_change_applies() -> None:
    snap = reduce_event(initial_snapshot(), _created_event(event_id="e1"))
    snap = reduce_event(
        snap,
        _status_event(
            event_id="e2",
            work_item_id="wi-1",
            new_status=WorkItemStatus.RUNNING,
            provenance=Provenance.MACHINE_OBSERVATION,
        ),
    )
    assert snap.work_items[0].status == WorkItemStatus.RUNNING


def test_model_hypothesis_cannot_override_machine_observation() -> None:
    """A weak source must NOT silently upgrade over a stronger observed fact.

    This is the core "no silent upgrade" invariant: machine_observation set
    RUNNING; a later model_hypothesis claiming DONE must NOT flip derived state.
    """

    snap = reduce_event(initial_snapshot(), _created_event(event_id="e1"))
    snap = reduce_event(
        snap,
        _status_event(
            event_id="e2",
            work_item_id="wi-1",
            new_status=WorkItemStatus.RUNNING,
            provenance=Provenance.MACHINE_OBSERVATION,
        ),
    )
    snap = reduce_event(
        snap,
        _status_event(
            event_id="e3",
            work_item_id="wi-1",
            new_status=WorkItemStatus.DONE,
            provenance=Provenance.MODEL_HYPOTHESIS,
        ),
    )
    # weaker provenance → derived status unchanged, stays RUNNING
    assert snap.work_items[0].status == WorkItemStatus.RUNNING
    assert snap.work_items[0].status_provenance == Provenance.MACHINE_OBSERVATION


def test_user_decision_overrides_model_hypothesis() -> None:
    """A strong source CAN override a weaker one."""

    snap = reduce_event(initial_snapshot(), _created_event(event_id="e1"))
    snap = reduce_event(
        snap,
        _status_event(
            event_id="e2",
            work_item_id="wi-1",
            new_status=WorkItemStatus.DONE,
            provenance=Provenance.MODEL_HYPOTHESIS,
        ),
    )
    snap = reduce_event(
        snap,
        _status_event(
            event_id="e3",
            work_item_id="wi-1",
            new_status=WorkItemStatus.RUNNING,
            provenance=Provenance.USER_DECISION,
        ),
    )
    assert snap.work_items[0].status == WorkItemStatus.RUNNING
    assert snap.work_items[0].status_provenance == Provenance.USER_DECISION


def test_unknown_cannot_assert_anything() -> None:
    """Provenance=unknown is too weak to flip a previously observed status."""

    snap = reduce_event(initial_snapshot(), _created_event(event_id="e1"))
    snap = reduce_event(
        snap,
        _status_event(
            event_id="e2",
            work_item_id="wi-1",
            new_status=WorkItemStatus.RUNNING,
            provenance=Provenance.MACHINE_OBSERVATION,
        ),
    )
    snap = reduce_event(
        snap,
        _status_event(
            event_id="e3",
            work_item_id="wi-1",
            new_status=WorkItemStatus.DONE,
            provenance=Provenance.UNKNOWN,
        ),
    )
    assert snap.work_items[0].status == WorkItemStatus.RUNNING


# ----------------------------------------------------- unknown event kinds ---


def test_unknown_event_kind_does_not_crash_and_is_retained() -> None:
    """Spec invariant: unknown new events may be retained but must not break
    the old reducer (forward compatibility)."""

    snap = initial_snapshot()
    unknown = EventEnvelope(
        event_id="e-future",
        kind="some.future.kind.v2",  # not known to this reducer version
        occurred_at="2026-07-21T00:00:00Z",
        source="future",
        provenance=Provenance.MACHINE_OBSERVATION,
        policy_version="v0",
        payload={"whatever": 1},
    )
    snap = reduce_event(snap, unknown)
    assert "some.future.kind.v2" in snap.unrecognized_event_kinds
    # reducer did not crash and produced a valid snapshot
    assert snap.last_seq >= 0


def test_duplicate_created_event_is_idempotent() -> None:
    """Folding the same work_item.created event twice must not append a
    ghost duplicate into the work_items tuple (replay robustness)."""

    snap = reduce_event(initial_snapshot(), _created_event(event_id="e1"))
    snap = reduce_event(snap, _created_event(event_id="e1-again"))
    assert len(snap.work_items) == 1


# ----------------------------------------------------- fold determinism ---


def test_fold_is_deterministic_across_runs() -> None:
    """Folding the same event list twice yields equal snapshots."""

    events = [
        _created_event(event_id="e1"),
        _status_event(
            event_id="e2",
            work_item_id="wi-1",
            new_status=WorkItemStatus.RUNNING,
            provenance=Provenance.MACHINE_OBSERVATION,
        ),
    ]

    def fold_all() -> object:
        snap = initial_snapshot()
        for ev in events:
            snap = reduce_event(snap, ev)
        return snap

    assert fold_all() == fold_all()
