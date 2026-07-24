from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from typing import TYPE_CHECKING, Any

from trowel_py.model_os.types import (
    CompletionEvidence,
    ErrorRecord,
    EventEnvelope,
    Provenance,
    TaskOrigin,
    TaskStatus,
    WaitingCondition,
    WaitingSubtype,
)


if TYPE_CHECKING:
    from trowel_py.model_os.reducer import Snapshot, TaskState


def _update_task(
    snap: Snapshot,
    task_id: str | None,
    current: TaskState,
    **updates: Any,
) -> Snapshot:
    return _replace_task(snap, task_id, replace(current, **updates))


def task_from_created(
    event: EventEnvelope,
    *,
    task_state_factory: Callable[..., TaskState],
) -> TaskState:
    p = event.payload
    return task_state_factory(
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
    if task_id is None:
        return None
    return next((task for task in snap.tasks if task.task_id == task_id), None)


def _replace_task(
    snap: Snapshot, task_id: str | None, new_state: TaskState
) -> Snapshot:
    if task_id is None:
        return snap
    # 畸形快照出现重复 ID 时沿用旧 reducer 语义，替换全部匹配位置。
    return replace(
        snap,
        tasks=tuple(
            new_state if task.task_id == task_id else task for task in snap.tasks
        ),
    )


def _waiting_from_payload(p: dict[str, Any]) -> WaitingCondition:
    raw_subtype = p.get("subtype")
    try:
        subtype = WaitingSubtype(raw_subtype) if raw_subtype else None
    except ValueError:
        # 未知枚举值向前兼容；其他异常继续上抛。
        subtype = None
    return WaitingCondition(
        kind=p["kind"],
        cause=p.get("cause", ""),
        subtype=subtype,
        episode_id=p.get("episode_id"),
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
    return _update_task(
        snap,
        event.task_id,
        current,
        **updates,
    )


def _apply_task_constraint_appended(snap: Snapshot, event: EventEnvelope) -> Snapshot:
    current = _find_task(snap, event.task_id)
    if current is None:
        return snap
    constraint = event.payload.get("constraint", "")
    if constraint in current.appended_constraints:
        return snap
    return _update_task(
        snap,
        event.task_id,
        current,
        appended_constraints=current.appended_constraints + (constraint,),
        updated_at=event.occurred_at,
    )


def _apply_task_warm_changed(snap: Snapshot, event: EventEnvelope) -> Snapshot:
    current = _find_task(snap, event.task_id)
    if current is None:
        return snap
    return _update_task(
        snap,
        event.task_id,
        current,
        warm=bool(event.payload["warm"]),
        updated_at=event.occurred_at,
    )


def _apply_task_warm_rank_set(snap: Snapshot, event: EventEnvelope) -> Snapshot:
    current = _find_task(snap, event.task_id)
    if current is None:
        return snap
    return _update_task(
        snap,
        event.task_id,
        current,
        warm_rank=event.payload.get("warm_rank"),
        updated_at=event.occurred_at,
    )


def _apply_task_waiting_set(snap: Snapshot, event: EventEnvelope) -> Snapshot:
    current = _find_task(snap, event.task_id)
    if current is None:
        return snap
    waiting = _waiting_from_payload(event.payload)
    return _update_task(
        snap,
        event.task_id,
        current,
        status=TaskStatus(waiting.kind),
        status_provenance=event.provenance,
        waiting_condition=waiting,
        updated_at=event.occurred_at,
    )


def _apply_task_waiting_cleared(snap: Snapshot, event: EventEnvelope) -> Snapshot:
    current = _find_task(snap, event.task_id)
    if current is None:
        return snap
    return _update_task(
        snap,
        event.task_id,
        current,
        status=TaskStatus.READY,
        status_provenance=event.provenance,
        waiting_condition=None,
        updated_at=event.occurred_at,
    )


def _apply_task_authorization_changed(snap: Snapshot, event: EventEnvelope) -> Snapshot:
    current = _find_task(snap, event.task_id)
    if current is None:
        return snap
    return _update_task(
        snap,
        event.task_id,
        current,
        authorization_scope=event.payload["authorization_scope"],
        updated_at=event.occurred_at,
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
    return _update_task(
        snap,
        event.task_id,
        current,
        status=TaskStatus.DONE,
        status_provenance=event.provenance,
        completion_evidence=evidence,
        warm=False,
        waiting_condition=None,
        updated_at=event.occurred_at,
    )


def _apply_task_cancelled(snap: Snapshot, event: EventEnvelope) -> Snapshot:
    current = _find_task(snap, event.task_id)
    if current is None:
        return snap
    return _update_task(
        snap,
        event.task_id,
        current,
        status=TaskStatus.CANCELLED,
        status_provenance=event.provenance,
        warm=False,
        waiting_condition=None,
        updated_at=event.occurred_at,
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
    return _update_task(
        snap,
        event.task_id,
        current,
        status=TaskStatus.ERROR,
        status_provenance=event.provenance,
        error_record=error,
        warm=False,
        waiting_condition=None,
        updated_at=event.occurred_at,
    )
