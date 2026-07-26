"""注意力调度触发、用户换班与幂等副作用编排。"""

from __future__ import annotations

import asyncio
import hashlib
from collections.abc import Awaitable, Callable
from dataclasses import replace
from datetime import datetime, timezone

from trowel_py.model_os.scheduling.journal import (
    append_schedule_terminal,
    complete_schedule_dispatch,
    read_recorded_schedule,
    read_resource_deferred,
    read_schedule_terminal,
    record_schedule_decision,
    resolve_foreground_request,
)
from trowel_py.model_os.scheduling.models import (
    RecordedScheduleDecision,
    ScheduleAction,
    ScheduleOutcome,
    ScheduleReason,
)
from trowel_py.model_os.scheduling.policy import decide_schedule
from trowel_py.model_os.scheduling.read_model import build_schedule_input
from trowel_py.model_os.store import TaskCommandError
from trowel_py.model_os.types import EventEnvelope, EventKind, Provenance

RequestUserPreempt = Callable[[str, str], Awaitable[str]]
ResumeSuspended = Callable[[object], Awaitable[str]]


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


class AttentionScheduler:
    def __init__(
        self,
        store,
        *,
        request_user_preempt: RequestUserPreempt,
        resume_suspended: ResumeSuspended | None = None,
    ) -> None:
        self._store = store
        self._request_user_preempt = request_user_preempt
        self._resume_suspended = resume_suspended
        self._lock = asyncio.Lock()

    def pending_override_task_id(self) -> str | None:
        resolved = {
            str(event.payload.get("request_event_id"))
            for _, event in self._store.list_events()
            if event.kind == EventKind.ATTENTION_FOREGROUND_RESOLVED
        }
        requested = [
            event
            for _, event in self._store.list_events()
            if event.kind == EventKind.ATTENTION_FOREGROUND_REQUESTED
        ]
        if not requested or requested[-1].event_id in resolved:
            return None
        return requested[-1].task_id

    async def request_foreground(
        self,
        task_id: str,
        *,
        idempotency_key: str,
    ) -> ScheduleOutcome:
        if not idempotency_key.strip():
            raise TaskCommandError("idempotency_key must be a non-empty string")
        identity = _hash(idempotency_key)[:24]
        event_id = f"event.attention.foreground_requested.{identity}"
        existing = next(
            (
                event
                for _, event in self._store.list_events()
                if event.event_id == event_id
            ),
            None,
        )
        if existing is not None:
            if (
                existing.kind != EventKind.ATTENTION_FOREGROUND_REQUESTED
                or existing.task_id != task_id
            ):
                raise TaskCommandError(
                    "idempotency_key is already bound to another foreground request"
                )
            return await self.trigger(existing.event_id)
        probe = build_schedule_input(
            self._store,
            trigger_event_ref="attention.validation",
        )
        valid = {item.task_id for item in probe.candidates}
        if probe.current_foreground_task_id != task_id and task_id not in valid:
            raise TaskCommandError("foreground target must be a ready warm Task")
        event = EventEnvelope(
            event_id=event_id,
            kind=EventKind.ATTENTION_FOREGROUND_REQUESTED,
            occurred_at=_now_iso(),
            source="user",
            provenance=Provenance.USER_DECISION,
            policy_version="attention-v0",
            payload={
                "target_task_id": task_id,
                "idempotency_key_hash": f"sha256:{_hash(idempotency_key)}",
            },
            task_id=task_id,
        )
        self._store.append_event(event)
        return await self.trigger(event.event_id)

    async def trigger(self, trigger_event_ref: str) -> ScheduleOutcome:
        async with self._lock:
            existing = next(
                (
                    decision
                    for _, decision in self._store.list_decisions()
                    if decision.kind == "attention.schedule"
                    and decision.cause_id == trigger_event_ref
                ),
                None,
            )
            if existing is not None:
                recorded = read_recorded_schedule(self._store, existing.decision_id)
                assert recorded is not None
                result = read_schedule_terminal(self._store, recorded)
                if result is None:
                    result = read_resource_deferred(self._store, recorded) or (
                        "configuration_required"
                        if recorded.decision.action is ScheduleAction.DISPATCH
                        else recorded.decision.reason.value
                    )
                return ScheduleOutcome(recorded, result)

            pending = self._pending_commands()
            if pending:
                return await self._continue_pending(pending[0])

            schedule_input = build_schedule_input(
                self._store,
                trigger_event_ref=trigger_event_ref,
                user_override_task_id=self.pending_override_task_id(),
            )
            decision = decide_schedule(schedule_input)
            trigger_seq = next(
                (
                    seq
                    for seq, event in self._store.list_events()
                    if event.event_id == trigger_event_ref
                ),
                None,
            )
            event_seqs = {
                event.event_id: seq for seq, event in self._store.list_events()
            }
            latest_processed_seq = max(
                (
                    event_seqs[item.cause_id]
                    for _, item in self._store.list_decisions()
                    if item.kind == "attention.schedule" and item.cause_id in event_seqs
                ),
                default=0,
            )
            if trigger_seq is not None and trigger_seq <= latest_processed_seq:
                decision = replace(
                    decision,
                    action=ScheduleAction.IDLE,
                    reason=ScheduleReason.STALE_TRIGGER,
                    target_work_item_id=None,
                    target_task_id=None,
                    target_episode_id=None,
                )
            recorded = record_schedule_decision(self._store, decision)
            if decision.reason is ScheduleReason.USER_OVERRIDE_CURRENT:
                resolve_foreground_request(
                    self._store,
                    recorded,
                    result_code="already_foreground",
                )
            if decision.action is ScheduleAction.REQUEST_YIELD:
                assert schedule_input.current_foreground_task_id is not None
                assert decision.target_task_id is not None
                try:
                    result = await self._request_user_preempt(
                        schedule_input.current_foreground_task_id,
                        decision.target_task_id,
                    )
                except BaseException:
                    append_schedule_terminal(
                        self._store,
                        recorded,
                        unknown_code="unknown_requires_reconcile",
                    )
                    raise
                append_schedule_terminal(self._store, recorded, result_code=result)
                return ScheduleOutcome(recorded, result)
            if decision.action is ScheduleAction.DISPATCH:
                if decision.target_episode_id is not None:
                    if self._resume_suspended is None:
                        return ScheduleOutcome(recorded, "resume_unavailable")
                    result = await self._resume_suspended(recorded)
                    return ScheduleOutcome(recorded, result)
                return ScheduleOutcome(recorded, "configuration_required")
            return ScheduleOutcome(recorded, decision.reason.value)

    def _pending_commands(self) -> list[RecordedScheduleDecision]:
        pending: list[RecordedScheduleDecision] = []
        for _, decision in self._store.list_decisions():
            if decision.kind != "attention.schedule":
                continue
            recorded = read_recorded_schedule(self._store, decision.decision_id)
            if (
                recorded is not None
                and recorded.correlation_id is not None
                and read_schedule_terminal(self._store, recorded) is None
            ):
                pending.append(recorded)
        return pending

    async def _continue_pending(
        self,
        recorded: RecordedScheduleDecision,
    ) -> ScheduleOutcome:
        decision = recorded.decision
        if decision.action is ScheduleAction.REQUEST_YIELD:
            append_schedule_terminal(
                self._store,
                recorded,
                unknown_code="unknown_requires_reconcile",
            )
            return ScheduleOutcome(recorded, "unknown_requires_reconcile")
        if decision.action is not ScheduleAction.DISPATCH:
            return ScheduleOutcome(recorded, decision.reason.value)
        if self._store.read_snapshot().foreground_task_id == decision.target_task_id:
            complete_schedule_dispatch(
                self._store,
                recorded.decision_id,
                episode_id=decision.target_episode_id,
            )
            return ScheduleOutcome(recorded, "foreground_claimed")
        if decision.target_episode_id is None:
            return ScheduleOutcome(recorded, "configuration_required")
        runnable = build_schedule_input(
            self._store,
            trigger_event_ref="attention.pending.validation",
        )
        if decision.target_task_id not in {
            item.task_id for item in runnable.candidates
        }:
            append_schedule_terminal(
                self._store,
                recorded,
                unknown_code="unknown_requires_reconcile",
            )
            return ScheduleOutcome(recorded, "unknown_requires_reconcile")
        if self._resume_suspended is None:
            return ScheduleOutcome(recorded, "resume_unavailable")
        result = await self._resume_suspended(recorded)
        return ScheduleOutcome(recorded, result)

    async def reconcile(self) -> tuple[ScheduleOutcome, ...]:
        outcomes: list[ScheduleOutcome] = []
        pending = self._pending_commands()
        for recorded in pending:
            outcomes.append(await self._continue_pending(recorded))
        if pending:
            return tuple(outcomes)
        boundary = self._store.journal_boundary()
        outcome = await self.trigger(
            f"attention.startup.{boundary.event_seq}.{boundary.decision_seq}"
        )
        return (outcome,)
