"""注意力调度的稳定 Decision 与 command journal。"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any

from trowel_py.model_os.scheduling.models import (
    ScheduleAction,
    ScheduleDecision,
    RecordedScheduleDecision,
)
from trowel_py.model_os.types import (
    DecisionDisposition,
    DecisionRecord,
    EventEnvelope,
    EventKind,
    Provenance,
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _hash(value: str) -> str:
    return f"sha256:{hashlib.sha256(value.encode('utf-8')).hexdigest()}"


def _candidate_summary(candidate, *, journal_event_seq: int) -> dict[str, object]:
    return {
        "work_item_id": candidate.work_item_id,
        "task_id": candidate.task_id,
        "suspended_episode_id": candidate.suspended_episode_id,
        "priority": candidate.priority,
        "warm_rank": candidate.warm_rank,
        "created_at_ref": _hash(candidate.created_at),
        "ready_epoch_ref": candidate.ready_epoch_ref,
        "virtual_service_segments": candidate.virtual_service_segments,
        "journal_event_seq": journal_event_seq,
    }


def _identity(decision: ScheduleDecision) -> str:
    body = {
        "action": decision.action.value,
        "reason": decision.reason.value,
        "trigger_event_ref": decision.trigger_event_ref,
        "journal_boundary": {
            "event_seq": decision.journal_boundary.event_seq,
            "decision_seq": decision.journal_boundary.decision_seq,
        },
        "target_work_item_id": decision.target_work_item_id,
        "target_task_id": decision.target_task_id,
        "target_episode_id": decision.target_episode_id,
        "candidates": [
            {
                "work_item_id": item.work_item_id,
                "task_id": item.task_id,
                "priority": item.priority,
                "warm_rank": item.warm_rank,
                "created_at": item.created_at,
                "ready_epoch_ref": item.ready_epoch_ref,
                "virtual_service_segments": item.virtual_service_segments,
                "suspended_episode_id": item.suspended_episode_id,
            }
            for item in decision.candidate_summaries
        ],
        "policy_version": decision.policy_version,
    }
    raw = json.dumps(body, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def read_recorded_schedule(store, decision_id: str) -> RecordedScheduleDecision | None:
    row = next(
        (item for _, item in store.list_decisions() if item.decision_id == decision_id),
        None,
    )
    if row is None:
        return None
    intent = next(
        (
            event
            for _, event in store.list_events()
            if event.kind == EventKind.COMMAND_INTENT and event.cause_id == decision_id
        ),
        None,
    )
    from trowel_py.model_os.journal import JournalBoundary
    from trowel_py.model_os.scheduling.models import (
        ScheduleCandidate,
        ScheduleReason,
    )

    candidates = tuple(
        ScheduleCandidate(
            work_item_id=str(item["work_item_id"]),
            task_id=str(item["task_id"]),
            priority=int(item["priority"]),
            warm_rank=(
                int(item["warm_rank"]) if item["warm_rank"] is not None else None
            ),
            created_at=str(item["created_at_ref"]),
            ready_epoch_ref=str(item["ready_epoch_ref"]),
            virtual_service_segments=int(item["virtual_service_segments"]),
            suspended_episode_id=(
                str(item["suspended_episode_id"])
                if item["suspended_episode_id"] is not None
                else None
            ),
        )
        for item in row.candidates
        if isinstance(item, dict)
    )
    schedule = ScheduleDecision(
        action=ScheduleAction(row.choice),
        reason=ScheduleReason(row.reason),
        trigger_event_ref=row.cause_id or "unknown",
        journal_boundary=JournalBoundary(
            event_seq=(
                int(row.candidates[0]["journal_event_seq"])
                if row.candidates and isinstance(row.candidates[0], dict)
                else 0
            ),
            decision_seq=0,
        ),
        candidate_summaries=candidates,
        policy_version=row.policy_version,
        target_work_item_id=row.work_item_id,
        target_task_id=row.task_id,
        target_episode_id=row.episode_id,
    )
    return RecordedScheduleDecision(
        decision_id=row.decision_id,
        correlation_id=row.correlation_id,
        intent_event_id=intent.event_id if intent is not None else None,
        decision=schedule,
    )


def record_schedule_decision(
    store, decision: ScheduleDecision
) -> RecordedScheduleDecision:
    identity = _identity(decision)
    decision_id = f"decision.attention.{identity}"
    execute = decision.action in {ScheduleAction.DISPATCH, ScheduleAction.REQUEST_YIELD}
    correlation_id = f"command.attention.{identity}" if execute else None
    record = DecisionRecord(
        decision_id=decision_id,
        kind="attention.schedule",
        disposition=(
            DecisionDisposition.EXECUTE if execute else DecisionDisposition.NO_ACTION
        ),
        decided_at=_now_iso(),
        signals={"refs": [decision.trigger_event_ref]},
        candidates=[
            _candidate_summary(
                item,
                journal_event_seq=decision.journal_boundary.event_seq,
            )
            for item in decision.candidate_summaries
        ],
        choice=decision.action.value,
        reason=decision.reason.value,
        policy_version=decision.policy_version,
        work_item_id=decision.target_work_item_id,
        task_id=decision.target_task_id,
        episode_id=decision.target_episode_id,
        cause_id=decision.trigger_event_ref,
        correlation_id=correlation_id,
    )
    if not execute:
        store.append_decision(record)
    else:
        raw = json.dumps(
            {
                "action": decision.action.value,
                "task_id": decision.target_task_id,
                "episode_id": decision.target_episode_id,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        intent = EventEnvelope(
            event_id=f"event.attention.intent.{identity}",
            kind=EventKind.COMMAND_INTENT,
            occurred_at=_now_iso(),
            source="attention_scheduler",
            provenance=Provenance.MACHINE_OBSERVATION,
            policy_version=decision.policy_version,
            payload={
                "command_kind": f"attention.{decision.action.value}",
                "target_ref": f"task.{decision.target_task_id}",
                "idempotency_key_hash": _hash(decision_id),
                "args_hash": _hash(raw),
            },
            work_item_id=decision.target_work_item_id,
            task_id=decision.target_task_id,
            episode_id=decision.target_episode_id,
            cause_id=decision_id,
            correlation_id=correlation_id,
        )
        store.append_decision_with_intent(record, intent)
    recorded = read_recorded_schedule(store, decision_id)
    assert recorded is not None
    return recorded


def append_schedule_terminal(
    store,
    recorded: RecordedScheduleDecision,
    *,
    result_code: str | None = None,
    unknown_code: str | None = None,
) -> None:
    if recorded.correlation_id is None:
        raise ValueError("schedule command has no correlation id")
    if (result_code is None) == (unknown_code is None):
        raise ValueError("exactly one schedule terminal code is required")
    identity = recorded.decision_id.rsplit(".", 1)[-1]
    payload: dict[str, Any]
    if result_code is not None:
        payload = {"result_code": result_code, "evidence_refs": []}
    else:
        assert unknown_code is not None
        payload = {"unknown_code": unknown_code, "evidence_refs": []}
    event = EventEnvelope(
        event_id=(
            f"event.attention.{'result' if result_code else 'unknown'}.{identity}"
        ),
        kind=EventKind.COMMAND_RESULT if result_code else EventKind.COMMAND_UNKNOWN,
        occurred_at=_now_iso(),
        source="attention_scheduler",
        provenance=Provenance.MACHINE_OBSERVATION,
        policy_version=recorded.decision.policy_version,
        payload=payload,
        work_item_id=recorded.decision.target_work_item_id,
        task_id=recorded.decision.target_task_id,
        episode_id=recorded.decision.target_episode_id,
        cause_id=recorded.decision_id,
        correlation_id=recorded.correlation_id,
    )
    store.append_event(event)


def read_schedule_terminal(store, recorded: RecordedScheduleDecision) -> str | None:
    terminal = next(
        (
            event
            for _, event in store.list_events()
            if event.cause_id == recorded.decision_id
            and event.kind in {EventKind.COMMAND_RESULT, EventKind.COMMAND_UNKNOWN}
        ),
        None,
    )
    if terminal is None:
        return None
    return str(
        terminal.payload.get("result_code")
        or terminal.payload.get("unknown_code")
        or "unknown_requires_reconcile"
    )


def record_resource_deferred(
    store,
    recorded: RecordedScheduleDecision,
    *,
    reason: str,
) -> str:
    identity = recorded.decision_id.rsplit(".", 1)[-1]
    store.append_event(
        EventEnvelope(
            event_id=f"event.attention.resource_deferred.{identity}.{reason}",
            kind=EventKind.ATTENTION_RESOURCE_DEFERRED,
            occurred_at=_now_iso(),
            source="attention_scheduler",
            provenance=Provenance.MACHINE_OBSERVATION,
            policy_version=recorded.decision.policy_version,
            payload={"reason": reason},
            work_item_id=recorded.decision.target_work_item_id,
            task_id=recorded.decision.target_task_id,
            episode_id=recorded.decision.target_episode_id,
            cause_id=recorded.decision_id,
            correlation_id=recorded.correlation_id,
        )
    )
    return f"resource_deferred.{reason}"


def read_resource_deferred(store, recorded: RecordedScheduleDecision) -> str | None:
    event = next(
        (
            item
            for _, item in reversed(store.list_events())
            if item.kind == EventKind.ATTENTION_RESOURCE_DEFERRED
            and item.cause_id == recorded.decision_id
        ),
        None,
    )
    if event is None:
        return None
    return f"resource_deferred.{event.payload['reason']}"


def complete_schedule_dispatch(
    store,
    decision_id: str,
    *,
    episode_id: str | None = None,
) -> None:
    recorded = read_recorded_schedule(store, decision_id)
    if recorded is None or recorded.decision.action is not ScheduleAction.DISPATCH:
        raise ValueError("unknown attention dispatch decision")
    if read_schedule_terminal(store, recorded) is None:
        append_schedule_terminal(store, recorded, result_code="foreground_claimed")
    if recorded.decision.target_task_id is not None:
        from trowel_py.model_os.scheduling.recovery import record_switch_started

        record_switch_started(
            store,
            decision_id=decision_id,
            task_id=recorded.decision.target_task_id,
            episode_id=episode_id or recorded.decision.target_episode_id or "unknown",
        )

    resolve_foreground_request(store, recorded, result_code="foreground_claimed")


def resolve_foreground_request(
    store,
    recorded: RecordedScheduleDecision,
    *,
    result_code: str,
) -> None:

    requested = [
        event
        for _, event in store.list_events()
        if event.kind == EventKind.ATTENTION_FOREGROUND_REQUESTED
        and event.task_id == recorded.decision.target_task_id
    ]
    if not requested:
        return
    request = requested[-1]
    identity = recorded.decision_id.rsplit(".", 1)[-1]
    store.append_event(
        EventEnvelope(
            event_id=f"event.attention.foreground_resolved.{identity}",
            kind=EventKind.ATTENTION_FOREGROUND_RESOLVED,
            occurred_at=_now_iso(),
            source="attention_scheduler",
            provenance=Provenance.MACHINE_OBSERVATION,
            policy_version=recorded.decision.policy_version,
            payload={
                "request_event_id": request.event_id,
                "result_code": result_code,
            },
            work_item_id=recorded.decision.target_work_item_id,
            task_id=recorded.decision.target_task_id,
            episode_id=recorded.decision.target_episode_id,
            cause_id=recorded.decision_id,
            correlation_id=recorded.correlation_id,
        )
    )
