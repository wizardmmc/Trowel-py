"""Soft yield request 的 command intent、结果与 compact 撤销审计。"""

from __future__ import annotations

from trowel_py.model_os.store import ModelOsStore
from trowel_py.model_os.types import (
    DecisionDisposition,
    DecisionRecord,
    EventEnvelope,
    EventKind,
    Provenance,
)
from trowel_py.model_os.yielding.journal import _hash, episode, now_iso, token
from trowel_py.model_os.yielding.models import SoftYieldPolicy, TurnState


def record_soft_request_intent(
    store: ModelOsStore,
    state: TurnState,
    policy: SoftYieldPolicy,
    *,
    used_tokens: int | None,
    window_tokens: int | None,
    ratio: float,
) -> tuple[str, str]:
    registration = state.registration
    identity = token(
        f"{registration.episode_id}:{registration.turn_id}:"
        f"{registration.generation}:{state.context_generation}"
    )
    decision_id = f"decision.yield.soft_request.{identity}"
    correlation_id = f"command.yield.soft_request.{identity}"
    current = episode(store, state)
    decision = DecisionRecord(
        decision_id=decision_id,
        kind="yield.soft_request",
        disposition=DecisionDisposition.EXECUTE,
        decided_at=now_iso(),
        signals={"refs": [f"context.generation.{state.context_generation}"]},
        candidates=["steer", "wait", "forced_deadline"],
        choice="steer",
        reason="context_soft_threshold",
        policy_version=policy.policy_version,
        budget_before={
            "used_tokens": used_tokens,
            "window_tokens": window_tokens,
            "ratio": ratio,
        },
        work_item_id=current.work_item_id,
        task_id=current.task_id,
        episode_id=registration.episode_id,
        correlation_id=correlation_id,
    )
    args = (
        f"{registration.turn_id}:{registration.generation}:"
        f"{state.context_generation}:{policy.threshold_ratio}"
    )
    intent = EventEnvelope(
        event_id=f"event.yield.soft_request.intent.{identity}",
        kind=EventKind.COMMAND_INTENT,
        occurred_at=now_iso(),
        source="kernel",
        provenance=Provenance.MACHINE_OBSERVATION,
        policy_version=policy.policy_version,
        payload={
            "command_kind": "yield.soft_request",
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


def append_soft_request_terminal(
    store: ModelOsStore,
    state: TurnState,
    policy: SoftYieldPolicy,
    *,
    unknown: bool,
) -> None:
    if state.soft_request_correlation_id is None:
        return
    suffix = "unknown" if unknown else "result"
    payload = (
        {"unknown_code": "unknown_requires_reconcile", "evidence_refs": []}
        if unknown
        else {"result_code": "runtime_accepted", "evidence_refs": []}
    )
    store.append_event(
        EventEnvelope(
            event_id=(
                f"event.yield.soft_request.{suffix}."
                f"{token(state.soft_request_correlation_id)}"
            ),
            kind=EventKind.COMMAND_UNKNOWN if unknown else EventKind.COMMAND_RESULT,
            occurred_at=now_iso(),
            source="episode_runner",
            provenance=Provenance.MACHINE_OBSERVATION,
            policy_version=policy.policy_version,
            payload=payload,
            episode_id=state.registration.episode_id,
            native_session_id=state.registration.native_session_id,
            cause_id=state.soft_request_decision_id,
            correlation_id=state.soft_request_correlation_id,
        )
    )


def record_soft_request_superseded(
    store: ModelOsStore,
    state: TurnState,
    policy: SoftYieldPolicy,
) -> None:
    current = episode(store, state)
    old_generation = state.soft_request_generation
    correlation_id = state.soft_request_correlation_id
    assert correlation_id is not None
    identity = token(
        f"{state.registration.episode_id}:{state.registration.turn_id}:"
        f"{old_generation}:superseded"
    )
    store.append_decision(
        DecisionRecord(
            decision_id=f"decision.yield.soft_request.superseded.{identity}",
            kind="yield.soft_request",
            disposition=DecisionDisposition.NO_ACTION,
            decided_at=now_iso(),
            signals={"refs": [correlation_id]},
            candidates=["continue_current_context", "supersede"],
            choice="supersede",
            reason="native_compaction_completed",
            policy_version=policy.policy_version,
            work_item_id=current.work_item_id,
            task_id=current.task_id,
            episode_id=current.episode_id,
        )
    )
