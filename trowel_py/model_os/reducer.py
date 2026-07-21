"""Pure reducer for the Model OS journal (slice-084).

The reducer folds ``EventEnvelope`` and ``DecisionRecord`` values into a
derived ``Snapshot``. It is a pure function: same inputs → same snapshot,
no I/O, no mutation. That purity is what makes replay idempotent (spec pass
criterion 1) and lets a snapshot be deleted and rebuilt from the journal.

Provenance rule (the concrete "no silent upgrade" invariant): a status claim
from a weaker source cannot overwrite one already held from a stronger
source. See ``Provenance.strength``. Weaker events are still in the journal
(audit) but do not flip derived state.

Forward compatibility: an event kind the reducer does not recognise is
appended to ``Snapshot.unrecognized_event_kinds`` rather than raising. A new
policy version that introduces new kinds therefore cannot break an old
reducer (spec: "未知新事件可保留但不能破坏旧 reducer").

``last_seq`` / ``last_decision_seq`` are NOT bumped here — they are position
markers owned by whoever drives the fold (the store stamps them from the
real SQLite seq during replay). The pure reducer leaves them untouched.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any

from trowel_py.model_os.types import (
    EventEnvelope,
    EventKind,
    MemoryEligibility,
    Provenance,
    SessionPurpose,
    WorkItemKind,
    WorkItemStatus,
)

_SCHEMA_VERSION = 1
_MAX_DESCRIPTION_LEN = 512


# ----------------------------------------------------------- derived state ---


@dataclass(frozen=True)
class WorkItemState:
    """Derived view of a WorkItem as folded from the event log.

    ``status_provenance`` records which provenance level set the current
    status, so the reducer can apply the no-silent-upgrade rule on the next
    status event.
    """

    work_item_id: str
    kind: WorkItemKind
    owner_ref: str
    task_id: str | None
    status: WorkItemStatus
    status_provenance: Provenance
    session_purpose: SessionPurpose
    memory_eligibility: MemoryEligibility


@dataclass(frozen=True)
class UnknownAction:
    """A recorded action whose outcome is not confirmed.

    Per spike-083: a side effect that may have happened but whose result was
    not written back is ``requires_reconcile``; a pending control channel
    lost on restart is ``requires_user_restart``. Both must be reconciled by
    reading reality — replay never auto-reissues them.
    """

    event_id: str
    work_item_id: str | None
    description: str
    reconcile_kind: str


@dataclass(frozen=True)
class Snapshot:
    """Derived journal state.

    ``work_items`` and ``unknown_actions`` are event-sourced (rebuildable
    from the log). ``active_leases`` is table-sourced and filled by the
    store at read time — leases are live operational state, not audit.
    """

    schema_version: int
    last_seq: int
    last_decision_seq: int
    work_items: tuple[WorkItemState, ...]
    active_leases: tuple[Any, ...]
    unknown_actions: tuple[UnknownAction, ...]
    unrecognized_event_kinds: tuple[str, ...]

    def task_work_items(self) -> tuple[WorkItemState, ...]:
        """Return only Task-kind work items (excludes all system work)."""

        return tuple(w for w in self.work_items if w.kind == WorkItemKind.TASK)


def initial_snapshot(schema_version: int = _SCHEMA_VERSION) -> Snapshot:
    """Return the empty snapshot every replay starts from."""

    return Snapshot(
        schema_version=schema_version,
        last_seq=0,
        last_decision_seq=0,
        work_items=(),
        active_leases=(),
        unknown_actions=(),
        unrecognized_event_kinds=(),
    )


# --------------------------------------------------------------- internals ---


def _work_item_from_created(event: EventEnvelope) -> WorkItemState:
    """Build a ``WorkItemState`` from a ``work_item.created`` payload.

    ``status_provenance`` starts at ``STALE`` (the weakest level): the
    creation event's provenance attests to the work item's EXISTENCE, not to
    a confident claim about its status. The PENDING status is a placeholder,
    so the first real status observation — at any provenance level — may
    override it. This keeps the no-silent-upgrade rule from accidentally
    locking a user-created work item out of all later machine/model
    observations.
    """

    payload = event.payload
    return WorkItemState(
        work_item_id=payload["work_item_id"],
        kind=WorkItemKind(payload["kind"]),
        owner_ref=payload["owner_ref"],
        task_id=payload.get("task_id"),
        status=WorkItemStatus(payload["status"]),
        status_provenance=Provenance.STALE,
        session_purpose=SessionPurpose(payload["session_purpose"]),
        memory_eligibility=MemoryEligibility(payload["memory_eligibility"]),
    )


def _replace_work_item(
    snap: Snapshot, work_item_id: str, new_state: WorkItemState
) -> Snapshot:
    """Return a snapshot with the given work item replaced (immutable)."""

    updated = tuple(
        new_state if w.work_item_id == work_item_id else w for w in snap.work_items
    )
    return replace(snap, work_items=updated)


def _apply_status_change(snap: Snapshot, event: EventEnvelope) -> Snapshot:
    """Apply a status change honouring the no-silent-upgrade rule.

    Two guards:
    - STALE cannot positively assert any status (it may only mark an existing
      claim as possibly outdated). So a stale status event never flips the
      derived status — not even over the STALE placeholder that ``created``
      leaves behind. This blocks the "stale observation silently turns
      PENDING into DONE" failure mode.
    - A weaker provenance cannot overwrite a stronger source's claim.
    """

    work_item_id = event.work_item_id
    if work_item_id is None:
        return snap
    current = next(
        (w for w in snap.work_items if w.work_item_id == work_item_id), None
    )
    if current is None:
        # status change for an unknown work item — retain as audit, no state
        return snap
    if event.provenance == Provenance.STALE:
        return snap
    if event.provenance.strength < current.status_provenance.strength:
        # weaker source cannot overwrite a stronger claim — no silent upgrade
        return snap
    new_state = replace(
        current,
        status=WorkItemStatus(event.payload["new_status"]),
        status_provenance=event.provenance,
    )
    return _replace_work_item(snap, work_item_id, new_state)


def _add_unknown_action(snap: Snapshot, event: EventEnvelope, kind: str) -> Snapshot:
    """Record an unknown-outcome action that awaits reconciliation.

    ``description`` is capped so a pathological payload cannot bloat the
    snapshot; the full payload is already in the journal for audit.
    """

    description = str(event.payload.get("description", ""))[:_MAX_DESCRIPTION_LEN]
    action = UnknownAction(
        event_id=event.event_id,
        work_item_id=event.work_item_id,
        description=description,
        reconcile_kind=kind,
    )
    return replace(snap, unknown_actions=snap.unknown_actions + (action,))


# --------------------------------------------------------------- public API ---


def reduce_event(snap: Snapshot, event: EventEnvelope) -> Snapshot:
    """Fold a single event into ``snap`` and return the new snapshot.

    Unknown event kinds are retained in ``unrecognized_event_kinds`` rather
    than raising, so a future policy version cannot break this reducer.
    """

    if event.kind == EventKind.WORK_ITEM_CREATED:
        work_item_id = event.payload.get("work_item_id")
        if any(w.work_item_id == work_item_id for w in snap.work_items):
            # idempotent fold: a replay that re-sees the same created event
            # must not append a ghost duplicate.
            return snap
        state = _work_item_from_created(event)
        return replace(snap, work_items=snap.work_items + (state,))
    if event.kind == EventKind.WORK_ITEM_STATUS_CHANGED:
        return _apply_status_change(snap, event)
    if event.kind == EventKind.SIDE_EFFECT_UNCONFIRMED:
        return _add_unknown_action(snap, event, "requires_reconcile")
    if event.kind == EventKind.PENDING_CHANNEL_LOST:
        return _add_unknown_action(snap, event, "requires_user_restart")
    if event.kind == EventKind.NOTE:
        return snap
    # forward-compat: retain the kind, do not crash
    if event.kind not in snap.unrecognized_event_kinds:
        return replace(
            snap,
            unrecognized_event_kinds=snap.unrecognized_event_kinds + (event.kind,),
        )
    return snap


def reduce_decision(snap: Snapshot, decision: Any) -> Snapshot:
    """Fold a decision record. Decisions are primarily audit; the reducer
    records that it has seen them without deriving opinionated state."""

    _ = decision  # audit-only for now; later slices may derive routing state
    return snap
