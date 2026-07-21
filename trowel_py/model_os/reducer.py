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
    CompletionEvidence,
    ErrorRecord,
    EventEnvelope,
    EventKind,
    MemoryEligibility,
    Provenance,
    SessionPurpose,
    TaskOrigin,
    TaskStatus,
    WaitingCondition,
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
class TaskState:
    """Derived view of a Task folded from the event log (slice-086).

    Mirrors ``WorkItemState`` but for the Task entity. Key difference from
    WorkItem: the Task status fold does NOT apply the no-silent-upgrade
    provenance rule (slice-086 grill decision 6). For WorkItem that rule
    prevents a stale observation silently flipping PENDING→DONE; for Task it
    would lock a user-created task (USER_DECISION) out of later
    machine-observed running/done transitions. Task transition authority is
    enforced at the command layer (structured entry points), not by
    provenance strength. ``status_provenance`` is retained here for audit
    only.
    """

    task_id: str
    origin: TaskOrigin
    original_goal: str
    appended_constraints: tuple[str, ...]
    status: TaskStatus
    status_provenance: Provenance
    priority: int
    warm: bool
    warm_rank: int | None
    authorization_scope: str
    waiting_condition: WaitingCondition | None
    completion_evidence: CompletionEvidence | None
    error_record: ErrorRecord | None
    primary_work_item_id: str | None
    created_at: str
    updated_at: str


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

    ``work_items``, ``tasks`` and ``unknown_actions`` are event-sourced
    (rebuildable from the log). ``active_leases`` and ``foreground_task_id``
    are table-sourced and filled by the store at read time — they are live
    operational state (who currently holds a resource / which task is
    foreground), not audit. Foreground follows the same pattern as leases:
    FOREGROUND_CLAIMED/RELEASED events are audit-only; the live owner lives
    in the foreground_claim table (slice-086).
    """

    schema_version: int
    last_seq: int
    last_decision_seq: int
    work_items: tuple[WorkItemState, ...]
    tasks: tuple[TaskState, ...]
    active_leases: tuple[Any, ...]
    foreground_task_id: str | None
    unknown_actions: tuple[UnknownAction, ...]
    unrecognized_event_kinds: tuple[str, ...]

    def task_work_items(self) -> tuple[WorkItemState, ...]:
        """Return only Task-kind work items (excludes all system work)."""

        return tuple(w for w in self.work_items if w.kind == WorkItemKind.TASK)

    def warm_tasks(self) -> tuple[TaskState, ...]:
        """Return warm Tasks (warm=True, not terminal), ordered by warm_rank
        (None sorts after set ranks, by created_at) — slice-086 §warm 集合.

        User-set ``warm_rank`` takes precedence; unset (None) tasks fall back
        to creation order and sort after explicitly-ranked ones.
        """

        warm = tuple(t for t in self.tasks if t.warm and not t.status.is_terminal)
        return tuple(
            sorted(
                warm,
                key=lambda t: (
                    t.warm_rank if t.warm_rank is not None else 10**9,
                    t.created_at,
                ),
            )
        )


def initial_snapshot(schema_version: int = _SCHEMA_VERSION) -> Snapshot:
    """Return the empty snapshot every replay starts from."""

    return Snapshot(
        schema_version=schema_version,
        last_seq=0,
        last_decision_seq=0,
        work_items=(),
        tasks=(),
        active_leases=(),
        foreground_task_id=None,
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


# ------------------------------------------------------------------- task fold


def _task_from_created(event: EventEnvelope) -> TaskState:
    """Build a TaskState from a task.created payload.

    status starts at BACKLOG, warm=False; primary_work_item_id is set when
    provided. ``status_provenance`` follows the event's provenance (audit
    only — the no-silent-upgrade rule does NOT apply to Task status; see
    ``TaskState`` docstring and slice-086 grill decision 6).
    """

    p = event.payload
    return TaskState(
        task_id=p["task_id"],
        origin=TaskOrigin(p["origin"]),
        original_goal=p["original_goal"],
        appended_constraints=tuple(p.get("appended_constraints", ())),
        status=TaskStatus(p.get("status", TaskStatus.BACKLOG.value)),
        status_provenance=event.provenance,
        priority=int(p.get("priority", 0)),
        warm=bool(p.get("warm", False)),
        warm_rank=p.get("warm_rank"),
        authorization_scope=p.get("authorization_scope", ""),
        waiting_condition=None,
        completion_evidence=None,
        error_record=None,
        primary_work_item_id=p.get("primary_work_item_id"),
        created_at=event.occurred_at,
        updated_at=event.occurred_at,
    )


def _find_task(snap: Snapshot, task_id: str | None) -> TaskState | None:
    """Return the TaskState with the given id, or None."""

    if task_id is None:
        return None
    return next((t for t in snap.tasks if t.task_id == task_id), None)


def _replace_task(
    snap: Snapshot, task_id: str | None, new_state: TaskState
) -> Snapshot:
    """Return a snapshot with the given Task replaced (immutable).

    ``task_id`` is ``str | None`` because callers pass ``event.task_id``
    (nullable on the envelope). ``None`` could only reach here for an event
    that did not match a known task, but the caller has already short-circuited
    via ``_find_task``; the None-guard is defence in depth.
    """

    if task_id is None:
        return snap
    return replace(
        snap,
        tasks=tuple(new_state if t.task_id == task_id else t for t in snap.tasks),
    )


def _waiting_from_payload(p: dict[str, Any]) -> WaitingCondition:
    """Reconstruct a WaitingCondition from a task.waiting_set payload."""

    return WaitingCondition(
        kind=p["kind"],
        cause=p.get("cause", ""),
        correlation_id=p.get("correlation_id"),
        deadline=p.get("deadline"),
        condition_kind=p.get("condition_kind"),
        target_ref=p.get("target_ref"),
        match_params=p.get("match_params"),
        open_question=p.get("open_question"),
        preparation_snapshot_ref=p.get("preparation_snapshot_ref"),
        earliest_review_at=p.get("earliest_review_at"),
    )


def _apply_task_status_change(snap: Snapshot, event: EventEnvelope) -> Snapshot:
    """Change a Task's status. No provenance-strength gate (slice-086 grill
    decision 6) — authority is enforced at the command layer, not here.

    When the new status leaves the waiting family (e.g. demote to backlog),
    ``waiting_condition`` is cleared — a non-waiting state must not carry
    stale waiting data (codex review M1)."""

    current = _find_task(snap, event.task_id)
    if current is None:
        return snap
    new_status = TaskStatus(event.payload["new_status"])
    updates: dict[str, Any] = {
        "status": new_status,
        "status_provenance": event.provenance,
        "updated_at": event.occurred_at,
    }
    if new_status not in (
        TaskStatus.WAITING_USER,
        TaskStatus.WAITING_EVENT,
        TaskStatus.INCUBATING,
    ):
        updates["waiting_condition"] = None
    return _replace_task(snap, event.task_id, replace(current, **updates))


def _apply_task_constraint_appended(snap: Snapshot, event: EventEnvelope) -> Snapshot:
    current = _find_task(snap, event.task_id)
    if current is None:
        return snap
    constraint = event.payload.get("constraint", "")
    if constraint in current.appended_constraints:
        return snap  # idempotent
    return _replace_task(
        snap,
        event.task_id,
        replace(
            current,
            appended_constraints=current.appended_constraints + (constraint,),
            updated_at=event.occurred_at,
        ),
    )


def _apply_task_warm_changed(snap: Snapshot, event: EventEnvelope) -> Snapshot:
    current = _find_task(snap, event.task_id)
    if current is None:
        return snap
    return _replace_task(
        snap,
        event.task_id,
        replace(
            current,
            warm=bool(event.payload["warm"]),
            updated_at=event.occurred_at,
        ),
    )


def _apply_task_warm_rank_set(snap: Snapshot, event: EventEnvelope) -> Snapshot:
    current = _find_task(snap, event.task_id)
    if current is None:
        return snap
    return _replace_task(
        snap,
        event.task_id,
        replace(
            current,
            warm_rank=event.payload.get("warm_rank"),
            updated_at=event.occurred_at,
        ),
    )


def _apply_task_waiting_set(snap: Snapshot, event: EventEnvelope) -> Snapshot:
    """Set waiting_condition AND move status to the waiting kind. One semantic
    event carries both (no paired status_changed)."""

    current = _find_task(snap, event.task_id)
    if current is None:
        return snap
    waiting = _waiting_from_payload(event.payload)
    return _replace_task(
        snap,
        event.task_id,
        replace(
            current,
            status=TaskStatus(waiting.kind),  # kind == "waiting_user" etc.
            status_provenance=event.provenance,
            waiting_condition=waiting,
            updated_at=event.occurred_at,
        ),
    )


def _apply_task_waiting_cleared(snap: Snapshot, event: EventEnvelope) -> Snapshot:
    current = _find_task(snap, event.task_id)
    if current is None:
        return snap
    return _replace_task(
        snap,
        event.task_id,
        replace(
            current,
            status=TaskStatus.READY,
            status_provenance=event.provenance,
            waiting_condition=None,
            updated_at=event.occurred_at,
        ),
    )


def _apply_task_authorization_changed(
    snap: Snapshot, event: EventEnvelope
) -> Snapshot:
    current = _find_task(snap, event.task_id)
    if current is None:
        return snap
    return _replace_task(
        snap,
        event.task_id,
        replace(
            current,
            authorization_scope=event.payload["authorization_scope"],
            updated_at=event.occurred_at,
        ),
    )


def _apply_task_completed(snap: Snapshot, event: EventEnvelope) -> Snapshot:
    current = _find_task(snap, event.task_id)
    if current is None:
        return snap
    p = event.payload
    evidence = CompletionEvidence(
        confirmed_by=p["confirmed_by"],
        confirmation_provenance=Provenance(p["confirmation_provenance"]),
        evidence_refs=tuple(p.get("evidence_refs", ())),
    )
    return _replace_task(
        snap,
        event.task_id,
        replace(
            current,
            status=TaskStatus.DONE,
            status_provenance=event.provenance,
            completion_evidence=evidence,
            warm=False,  # terminal → leaves warm
            waiting_condition=None,  # terminal → no pending wait
            updated_at=event.occurred_at,
        ),
    )


def _apply_task_cancelled(snap: Snapshot, event: EventEnvelope) -> Snapshot:
    current = _find_task(snap, event.task_id)
    if current is None:
        return snap
    return _replace_task(
        snap,
        event.task_id,
        replace(
            current,
            status=TaskStatus.CANCELLED,
            status_provenance=event.provenance,
            warm=False,
            waiting_condition=None,
            updated_at=event.occurred_at,
        ),
    )


def _apply_task_error_recorded(snap: Snapshot, event: EventEnvelope) -> Snapshot:
    current = _find_task(snap, event.task_id)
    if current is None:
        return snap
    p = event.payload
    error = ErrorRecord(
        origin=TaskOrigin(p["origin"]),
        failure_reason=p.get("failure_reason", ""),
        last_episode_ref=p.get("last_episode_ref"),
        last_snapshot_ref=p.get("last_snapshot_ref"),
        recovery_hint=p.get("recovery_hint"),
    )
    return _replace_task(
        snap,
        event.task_id,
        replace(
            current,
            status=TaskStatus.ERROR,
            status_provenance=event.provenance,
            error_record=error,
            warm=False,
            waiting_condition=None,
            updated_at=event.occurred_at,
        ),
    )


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
    if event.kind == EventKind.SELF_CHANGE_PROPOSED:
        # slice-085: a model's proposed Self change is recorded for audit but
        # NEVER applied — Self is assembled from runtime facts
        # (``self_assembler``), not derived from events. Structural anti-forgery
        # (pass 4): no event, regardless of provenance, can alter Self state.
        return snap
    # slice-086 — Task fold. Tasks are event-sourced like work_items.
    if event.kind == EventKind.TASK_CREATED:
        task_id = event.payload.get("task_id")
        if any(t.task_id == task_id for t in snap.tasks):
            return snap  # idempotent replay
        return replace(snap, tasks=snap.tasks + (_task_from_created(event),))
    if event.kind == EventKind.TASK_STATUS_CHANGED:
        return _apply_task_status_change(snap, event)
    if event.kind == EventKind.TASK_CONSTRAINT_APPENDED:
        return _apply_task_constraint_appended(snap, event)
    if event.kind == EventKind.TASK_WARM_CHANGED:
        return _apply_task_warm_changed(snap, event)
    if event.kind == EventKind.TASK_WARM_RANK_SET:
        return _apply_task_warm_rank_set(snap, event)
    if event.kind == EventKind.TASK_WAITING_SET:
        return _apply_task_waiting_set(snap, event)
    if event.kind == EventKind.TASK_WAITING_CLEARED:
        return _apply_task_waiting_cleared(snap, event)
    if event.kind == EventKind.TASK_AUTHORIZATION_CHANGED:
        return _apply_task_authorization_changed(snap, event)
    if event.kind == EventKind.TASK_COMPLETED:
        return _apply_task_completed(snap, event)
    if event.kind == EventKind.TASK_CANCELLED:
        return _apply_task_cancelled(snap, event)
    if event.kind == EventKind.TASK_ERROR_RECORDED:
        return _apply_task_error_recorded(snap, event)
    if event.kind in (
        EventKind.TASK_CREATION_DENIED,
        EventKind.FOREGROUND_CLAIMED,
        EventKind.FOREGROUND_RELEASED,
    ):
        # audit-only. FOREGROUND_CLAIMED/RELEASED never derive state: the
        # live foreground owner lives in the foreground_claim table (read at
        # snapshot time, same pattern as active_leases). TASK_CREATION_DENIED
        # records that a MODEL_HYPOTHESIS creation attempt was refused —
        # retained for traceability, affects no derived state.
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
