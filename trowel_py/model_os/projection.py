"""Event-derived Snapshot projection 的显式 JSON codec。"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import replace
from typing import Any

from trowel_py.model_os.context_observer import (
    context_sample_from_dict,
    context_sample_to_dict,
)
from trowel_py.model_os.reducer import (
    ContextObservationState,
    EpisodeState,
    Snapshot,
    TaskState,
    UnknownAction,
    WorkItemState,
)
from trowel_py.model_os.types import (
    CompletionEvidence,
    EpisodeStatus,
    ErrorRecord,
    MemoryEligibility,
    PendingDescriptor,
    Provenance,
    ReconcileReason,
    SessionPurpose,
    SnapshotRef,
    TaskOrigin,
    TaskStatus,
    WaitingCondition,
    WaitingSubtype,
    WorkItemKind,
    WorkItemStatus,
)
from trowel_py.model_os.journal import JournalBoundary


PROJECTION_NAME = "model_os.snapshot"
PROJECTION_VERSION = 1


def _waiting_to_dict(value: WaitingCondition | None) -> dict[str, Any] | None:
    if value is None:
        return None
    return {
        "kind": value.kind,
        "cause": value.cause,
        "subtype": value.subtype.value if value.subtype is not None else None,
        "episode_id": value.episode_id,
        "correlation_id": value.correlation_id,
        "deadline": value.deadline,
        "condition_kind": value.condition_kind,
        "target_ref": value.target_ref,
        "match_params": value.match_params,
        "open_question": value.open_question,
        "preparation_snapshot_ref": value.preparation_snapshot_ref,
        "earliest_review_at": value.earliest_review_at,
    }


def _waiting_from_dict(value: dict[str, Any] | None) -> WaitingCondition | None:
    if value is None:
        return None
    subtype = value.get("subtype")
    return WaitingCondition(
        kind=str(value["kind"]),
        cause=str(value["cause"]),
        subtype=WaitingSubtype(subtype) if subtype is not None else None,
        episode_id=value.get("episode_id"),
        correlation_id=value.get("correlation_id"),
        deadline=value.get("deadline"),
        condition_kind=value.get("condition_kind"),
        target_ref=value.get("target_ref"),
        match_params=value.get("match_params"),
        open_question=value.get("open_question"),
        preparation_snapshot_ref=value.get("preparation_snapshot_ref"),
        earliest_review_at=value.get("earliest_review_at"),
    )


def _pending_to_dict(value: PendingDescriptor | None) -> dict[str, Any] | None:
    if value is None:
        return None
    return {
        "kind": value.kind.value,
        "native_generation": value.native_generation,
        "correlation_id": value.correlation_id,
        "cause": value.cause,
        "posed_at": value.posed_at,
    }


def _pending_from_dict(value: dict[str, Any] | None) -> PendingDescriptor | None:
    if value is None:
        return None
    return PendingDescriptor(
        kind=WaitingSubtype(value["kind"]),
        native_generation=value.get("native_generation"),
        correlation_id=str(value["correlation_id"]),
        cause=str(value["cause"]),
        posed_at=str(value["posed_at"]),
    )


def _snapshot_ref_to_dict(value: SnapshotRef | None) -> dict[str, Any] | None:
    if value is None:
        return None
    return {
        "episode_id": value.episode_id,
        "version": value.version,
        "committed_event_id": value.committed_event_id,
        "payload_hash": value.payload_hash,
    }


def _snapshot_ref_from_dict(value: dict[str, Any] | None) -> SnapshotRef | None:
    if value is None:
        return None
    return SnapshotRef(
        episode_id=str(value["episode_id"]),
        version=int(value["version"]),
        committed_event_id=str(value["committed_event_id"]),
        payload_hash=str(value["payload_hash"]),
    )


def snapshot_to_json(snapshot: Snapshot) -> str:
    """编码时强制清空实时字段，checkpoint 不能缓存 lease 或 foreground。"""

    event_state = replace(snapshot, active_leases=(), foreground_task_id=None)
    body = {
        "schema_version": event_state.schema_version,
        "last_seq": event_state.last_seq,
        "last_decision_seq": event_state.last_decision_seq,
        "work_items": [
            {
                "work_item_id": item.work_item_id,
                "kind": item.kind.value,
                "owner_ref": item.owner_ref,
                "task_id": item.task_id,
                "status": item.status.value,
                "status_provenance": item.status_provenance.value,
                "session_purpose": item.session_purpose.value,
                "memory_eligibility": item.memory_eligibility.value,
            }
            for item in event_state.work_items
        ],
        "tasks": [
            {
                "task_id": item.task_id,
                "origin": item.origin.value,
                "original_goal": item.original_goal,
                "appended_constraints": list(item.appended_constraints),
                "status": item.status.value,
                "status_provenance": item.status_provenance.value,
                "priority": item.priority,
                "warm": item.warm,
                "warm_rank": item.warm_rank,
                "authorization_scope": item.authorization_scope,
                "waiting_condition": _waiting_to_dict(item.waiting_condition),
                "completion_evidence": (
                    {
                        "confirmed_by": item.completion_evidence.confirmed_by,
                        "confirmation_provenance": (
                            item.completion_evidence.confirmation_provenance.value
                        ),
                        "evidence_refs": list(item.completion_evidence.evidence_refs),
                    }
                    if item.completion_evidence is not None
                    else None
                ),
                "error_record": (
                    {
                        "origin": item.error_record.origin.value,
                        "failure_reason": item.error_record.failure_reason,
                        "last_episode_ref": item.error_record.last_episode_ref,
                        "last_snapshot_ref": item.error_record.last_snapshot_ref,
                        "recovery_hint": item.error_record.recovery_hint,
                    }
                    if item.error_record is not None
                    else None
                ),
                "primary_work_item_id": item.primary_work_item_id,
                "created_at": item.created_at,
                "updated_at": item.updated_at,
            }
            for item in event_state.tasks
        ],
        "episodes": [
            {
                "episode_id": item.episode_id,
                "work_item_id": item.work_item_id,
                "task_id": item.task_id,
                "status": item.status.value,
                "status_provenance": item.status_provenance.value,
                "native_session_id": item.native_session_id,
                "pending_descriptor": _pending_to_dict(item.pending_descriptor),
                "reconcile_reason": (
                    item.reconcile_reason.value
                    if item.reconcile_reason is not None
                    else None
                ),
                "last_snapshot_ref": _snapshot_ref_to_dict(item.last_snapshot_ref),
                "created_at": item.created_at,
                "updated_at": item.updated_at,
            }
            for item in event_state.episodes
        ],
        "active_leases": [],
        "foreground_task_id": None,
        "unknown_actions": [
            {
                "event_id": item.event_id,
                "work_item_id": item.work_item_id,
                "description": item.description,
                "reconcile_kind": item.reconcile_kind,
            }
            for item in event_state.unknown_actions
        ],
        "unrecognized_event_kinds": list(event_state.unrecognized_event_kinds),
        "context_observations": [
            {
                "episode_id": item.episode_id,
                "native_session_id": item.native_session_id,
                "generation": item.generation,
                "latest_sample": context_sample_to_dict(item.latest_sample),
                "observed_at": item.observed_at,
            }
            for item in event_state.context_observations
        ],
    }
    return json.dumps(
        body,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def snapshot_hash(state_json: str) -> str:
    return f"sha256:{hashlib.sha256(state_json.encode('utf-8')).hexdigest()}"


def snapshot_from_json(state_json: str) -> Snapshot:
    body = json.loads(state_json)
    if not isinstance(body, dict):
        raise ValueError("projection state must be an object")
    work_items = tuple(
        WorkItemState(
            work_item_id=str(item["work_item_id"]),
            kind=WorkItemKind(item["kind"]),
            owner_ref=str(item["owner_ref"]),
            task_id=item.get("task_id"),
            status=WorkItemStatus(item["status"]),
            status_provenance=Provenance(item["status_provenance"]),
            session_purpose=SessionPurpose(item["session_purpose"]),
            memory_eligibility=MemoryEligibility(item["memory_eligibility"]),
        )
        for item in body["work_items"]
    )
    tasks: list[TaskState] = []
    for item in body["tasks"]:
        completion = item.get("completion_evidence")
        error = item.get("error_record")
        tasks.append(
            TaskState(
                task_id=str(item["task_id"]),
                origin=TaskOrigin(item["origin"]),
                original_goal=str(item["original_goal"]),
                appended_constraints=tuple(item["appended_constraints"]),
                status=TaskStatus(item["status"]),
                status_provenance=Provenance(item["status_provenance"]),
                priority=int(item["priority"]),
                warm=bool(item["warm"]),
                warm_rank=(
                    int(item["warm_rank"]) if item.get("warm_rank") is not None else None
                ),
                authorization_scope=str(item["authorization_scope"]),
                waiting_condition=_waiting_from_dict(item.get("waiting_condition")),
                completion_evidence=(
                    CompletionEvidence(
                        confirmed_by=str(completion["confirmed_by"]),
                        confirmation_provenance=Provenance(
                            completion["confirmation_provenance"]
                        ),
                        evidence_refs=tuple(completion["evidence_refs"]),
                    )
                    if completion is not None
                    else None
                ),
                error_record=(
                    ErrorRecord(
                        origin=TaskOrigin(error["origin"]),
                        failure_reason=str(error["failure_reason"]),
                        last_episode_ref=error.get("last_episode_ref"),
                        last_snapshot_ref=error.get("last_snapshot_ref"),
                        recovery_hint=error.get("recovery_hint"),
                    )
                    if error is not None
                    else None
                ),
                primary_work_item_id=item.get("primary_work_item_id"),
                created_at=str(item["created_at"]),
                updated_at=str(item["updated_at"]),
            )
        )
    episodes = tuple(
        EpisodeState(
            episode_id=str(item["episode_id"]),
            work_item_id=str(item["work_item_id"]),
            task_id=item.get("task_id"),
            status=EpisodeStatus(item["status"]),
            status_provenance=Provenance(item["status_provenance"]),
            native_session_id=item.get("native_session_id"),
            pending_descriptor=_pending_from_dict(item.get("pending_descriptor")),
            reconcile_reason=(
                ReconcileReason(item["reconcile_reason"])
                if item.get("reconcile_reason") is not None
                else None
            ),
            last_snapshot_ref=_snapshot_ref_from_dict(item.get("last_snapshot_ref")),
            created_at=str(item["created_at"]),
            updated_at=str(item["updated_at"]),
        )
        for item in body["episodes"]
    )
    unknown_actions = tuple(
        UnknownAction(
            event_id=str(item["event_id"]),
            work_item_id=item.get("work_item_id"),
            description=str(item["description"]),
            reconcile_kind=str(item["reconcile_kind"]),
        )
        for item in body["unknown_actions"]
    )
    observations = tuple(
        ContextObservationState(
            episode_id=item.get("episode_id"),
            native_session_id=str(item["native_session_id"]),
            generation=int(item["generation"]),
            latest_sample=context_sample_from_dict(
                item["latest_sample"], str(item["native_session_id"])
            ),
            observed_at=str(item["observed_at"]),
        )
        for item in body["context_observations"]
    )
    if body.get("active_leases") != [] or body.get("foreground_task_id") is not None:
        raise ValueError("projection contains live state")
    return Snapshot(
        schema_version=int(body["schema_version"]),
        last_seq=int(body["last_seq"]),
        last_decision_seq=int(body["last_decision_seq"]),
        work_items=work_items,
        tasks=tuple(tasks),
        episodes=episodes,
        active_leases=(),
        foreground_task_id=None,
        unknown_actions=unknown_actions,
        unrecognized_event_kinds=tuple(body["unrecognized_event_kinds"]),
        context_observations=observations,
    )


def load_snapshot_checkpoint(
    conn: sqlite3.Connection,
    boundary: JournalBoundary,
) -> tuple[Snapshot, JournalBoundary] | None:
    row = conn.execute(
        "SELECT * FROM projection_checkpoints "
        "WHERE projection_name=? AND projection_version=? "
        "AND event_seq <= ? AND decision_seq <= ? "
        "ORDER BY event_seq DESC, decision_seq DESC LIMIT 1",
        (
            PROJECTION_NAME,
            PROJECTION_VERSION,
            boundary.event_seq,
            boundary.decision_seq,
        ),
    ).fetchone()
    if row is None:
        return None
    try:
        state_json = str(row["state_json"])
        if snapshot_hash(state_json) != row["state_hash"]:
            return None
        snapshot = snapshot_from_json(state_json)
        checkpoint_boundary = JournalBoundary(
            event_seq=int(row["event_seq"]),
            decision_seq=int(row["decision_seq"]),
        )
        if (
            snapshot.last_seq != checkpoint_boundary.event_seq
            or snapshot.last_decision_seq != checkpoint_boundary.decision_seq
        ):
            return None
        return snapshot, checkpoint_boundary
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        return None


def upsert_snapshot_checkpoint(
    conn: sqlite3.Connection,
    *,
    boundary: JournalBoundary,
    state_json: str,
    state_hash: str,
    created_at: str,
) -> None:
    conn.execute(
        "INSERT INTO projection_checkpoints ("
        "projection_name, projection_version, event_seq, decision_seq, "
        "state_json, state_hash, created_at) VALUES (?, ?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(projection_name, projection_version, event_seq, decision_seq) "
        "DO UPDATE SET state_json=excluded.state_json, "
        "state_hash=excluded.state_hash, created_at=excluded.created_at",
        (
            PROJECTION_NAME,
            PROJECTION_VERSION,
            boundary.event_seq,
            boundary.decision_seq,
            state_json,
            state_hash,
            created_at,
        ),
    )
