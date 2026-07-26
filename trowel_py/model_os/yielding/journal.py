"""Yield 的 DecisionRecord、command intent 与 terminal 审计。"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict
from datetime import datetime, timezone
from uuid import uuid4
from typing import Any

from trowel_py.model_os.store import ModelOsStore
from trowel_py.model_os.types import (
    DecisionDisposition,
    DecisionRecord,
    EventEnvelope,
    EventKind,
    Provenance,
)
from trowel_py.model_os.yielding.models import (
    ForceYieldReason,
    TurnState,
    YieldProposal,
)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def token(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:24]


def proposal_ref(proposal: YieldProposal) -> str:
    body = asdict(proposal)
    body["suggested_task_state"] = proposal.suggested_task_state.value
    encoded = json.dumps(
        body,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return f"proposal.{token(encoded)}"


def _hash(value: str) -> str:
    return f"sha256:{hashlib.sha256(value.encode('utf-8')).hexdigest()}"


def episode(store: ModelOsStore, state: TurnState):
    current = store.read_snapshot().episode_by_id(state.registration.episode_id)
    if current is None:
        raise RuntimeError("registered Episode disappeared from the journal")
    return current


def record_no_action(
    store: ModelOsStore,
    state: TurnState,
    *,
    kind: str,
    choice: str,
    reason: str,
    signal_refs: list[str] | None = None,
    details: dict[str, Any] | None = None,
) -> None:
    current = episode(store, state)
    candidate: dict[str, Any] = {"action": choice}
    if state.force_reason is not None:
        candidate["force_reason"] = state.force_reason.value
    if details:
        candidate.update(details)
    identity = (
        token(f"{state.registration.episode_id}:{state.registration.turn_id}:proposal")
        if kind == "yield.proposal"
        else uuid4().hex
    )
    store.append_decision(
        DecisionRecord(
            decision_id=f"decision.{kind}.{identity}",
            kind=kind,
            disposition=DecisionDisposition.NO_ACTION,
            decided_at=now_iso(),
            signals={"refs": signal_refs or []},
            candidates=[candidate],
            choice=choice,
            reason=reason,
            policy_version="yield-v1",
            work_item_id=current.work_item_id,
            task_id=current.task_id,
            episode_id=current.episode_id,
        )
    )


def record_missing_turn(
    store: ModelOsStore, requested_reason: ForceYieldReason
) -> None:
    store.append_decision(
        DecisionRecord(
            decision_id=f"decision.yield.interrupt.{uuid4().hex}",
            kind="yield.interrupt",
            disposition=DecisionDisposition.NO_ACTION,
            decided_at=now_iso(),
            signals={"refs": []},
            candidates=[
                {"action": "interrupt", "force_reason": requested_reason.value}
            ],
            choice="no_action",
            reason="no_active_turn",
            policy_version="yield-v1",
        )
    )


def record_interrupt_intent(
    store: ModelOsStore, state: TurnState
) -> tuple[str, str]:
    registration = state.registration
    reason = state.force_reason
    assert reason is not None
    identity = token(
        f"{registration.episode_id}:{registration.turn_id}:"
        f"{registration.generation}:{reason.value}"
    )
    decision_id = f"decision.yield.interrupt.{identity}"
    correlation_id = f"command.yield.interrupt.{identity}"
    current = episode(store, state)
    decision = DecisionRecord(
        decision_id=decision_id,
        kind="yield.interrupt",
        disposition=DecisionDisposition.EXECUTE,
        decided_at=now_iso(),
        signals={"refs": []},
        candidates=["interrupt", "defer"],
        choice="interrupt",
        reason=reason.value,
        policy_version="yield-v1",
        work_item_id=current.work_item_id,
        task_id=current.task_id,
        episode_id=registration.episode_id,
        correlation_id=correlation_id,
    )
    args = f"{registration.turn_id}:{registration.generation}:{reason.value}"
    intent = EventEnvelope(
        event_id=f"event.yield.interrupt.intent.{identity}",
        kind=EventKind.COMMAND_INTENT,
        occurred_at=now_iso(),
        source="kernel",
        provenance=Provenance.MACHINE_OBSERVATION,
        policy_version="yield-v1",
        payload={
            "command_kind": "yield.interrupt",
            "target_ref": f"episode.{registration.episode_id}",
            "idempotency_key_hash": _hash(correlation_id),
            "args_hash": _hash(args),
        },
        work_item_id=current.work_item_id,
        task_id=current.task_id,
        episode_id=registration.episode_id,
        native_session_id=registration.native_session_id,
        cause_id=decision_id,
        correlation_id=correlation_id,
    )
    store.append_decision_with_intent(decision, intent)
    return decision_id, correlation_id


def record_boundary(
    store: ModelOsStore,
    state: TurnState,
    *,
    choice: str,
    elapsed_ms: int,
) -> None:
    current = episode(store, state)
    identity = token(
        f"{state.registration.episode_id}:{state.registration.turn_id}:{choice}"
    )
    store.append_decision(
        DecisionRecord(
            decision_id=f"decision.yield.boundary.{identity}",
            kind="yield.boundary",
            disposition=DecisionDisposition.NO_ACTION,
            decided_at=now_iso(),
            signals={"refs": []},
            candidates=[
                {
                    "force_reason": (
                        state.force_reason.value if state.force_reason else "cooperative"
                    ),
                    "terminal": state.terminal_type or "unknown",
                }
            ],
            choice=choice,
            reason="safe_boundary_reached",
            policy_version="yield-v1",
            budget_before={"delay_ms": max(0, elapsed_ms)},
            work_item_id=current.work_item_id,
            task_id=current.task_id,
            episode_id=current.episode_id,
        )
    )


def append_command_terminal(
    store: ModelOsStore, state: TurnState, *, unknown: bool
) -> None:
    if state.interrupt_correlation_id is None or state.interrupt_decision_id is None:
        return
    kind = EventKind.COMMAND_UNKNOWN if unknown else EventKind.COMMAND_RESULT
    payload = (
        {"unknown_code": "unknown_requires_reconcile", "evidence_refs": []}
        if unknown
        else {"result_code": "terminal_observed", "evidence_refs": []}
    )
    store.append_event(
        EventEnvelope(
            event_id=(
                "event.yield.interrupt.terminal."
                f"{token(state.interrupt_correlation_id)}"
            ),
            kind=kind,
            occurred_at=now_iso(),
            source="episode_runner",
            provenance=Provenance.MACHINE_OBSERVATION,
            policy_version="yield-v1",
            payload=payload,
            episode_id=state.registration.episode_id,
            native_session_id=state.registration.native_session_id,
            cause_id=state.interrupt_decision_id,
            correlation_id=state.interrupt_correlation_id,
        )
    )
