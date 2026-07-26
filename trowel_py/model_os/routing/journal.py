"""Route Decision 与结构化复核的现有 journal 适配。"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone

from trowel_py.model_os.routing.models import (
    RecordedRouteDecision,
    RouteDecision,
    RouteInput,
    RouteReviewClass,
)
from trowel_py.model_os.cognitive_signals import Reliability, SignalFamily
from trowel_py.model_os.signal_projection import read_signal_from_event_payload
from trowel_py.model_os.journal import JournalIdentityConflict
from trowel_py.model_os.types import EventEnvelope, EventKind, Provenance
from trowel_py.model_os.types import DecisionRecord
from trowel_py.model_os.work_broker import ModelTier

ROUTE_DECISION_KIND = "cognitive.route"
ROUTE_ACTUAL_KIND = "route.actual_observed"
ROUTE_REVIEW_KIND = "route.review_recorded"
ROUTE_APPROVAL_KIND = "route.activation_approved"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _token(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:24]


def _hash(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()


def _candidate(role: str, value) -> dict[str, object]:
    return {
        "role": role,
        "tier": value.tier.value if value.tier is not None else None,
        "model": value.model,
        "effort": value.effort,
        "budget_cap": value.budget_cap,
        "request_model": value.request_model,
    }


def _route_input_candidate(route_input: RouteInput) -> dict[str, object]:
    value: dict[str, object] = {
        "role": "input",
        "work_item_id": route_input.work_item_id,
        "task_id": route_input.task_id,
        "runtime": route_input.runtime,
        "preference": route_input.user_preference.value,
        "mode": route_input.mode.value,
        "evaluation_domain": route_input.evaluation_domain,
        "mandatory_markers": [item.value for item in route_input.mandatory_markers],
        "trusted_pre_route_markers": [
            item.value for item in route_input.trusted_pre_route_markers
        ],
        "trusted_outcomes": [item.value for item in route_input.trusted_outcomes],
        "previous_tier": (
            route_input.previous_tier.value
            if route_input.previous_tier is not None
            else None
        ),
        "fixed_model": route_input.fixed_model,
        "fixed_effort": route_input.fixed_effort,
        "candidates": [
            _candidate("configured", item) for item in route_input.candidates
        ],
        "input_fact_refs": list(route_input.input_fact_refs),
        "canary_approved": route_input.canary_approved,
    }
    value["input_hash"] = _hash(
        json.dumps(value, sort_keys=True, separators=(",", ":"))
    )
    return value


def record_route_decision(
    store,
    idempotency_key: str,
    route_input: RouteInput,
    decision: RouteDecision,
) -> RecordedRouteDecision:
    if not idempotency_key.strip():
        raise ValueError("route idempotency_key must be non-empty")
    identity = _token(idempotency_key)
    decision_id = f"decision.route.{identity}"
    input_candidate = _route_input_candidate(route_input)
    existing = next(
        (item for _, item in store.list_decisions() if item.decision_id == decision_id),
        None,
    )
    if existing is not None:
        existing_input = next(
            (
                item
                for item in existing.candidates
                if isinstance(item, dict) and item.get("role") == "input"
            ),
            None,
        )
        if (
            existing_input is None
            or existing_input.get("input_hash") != input_candidate["input_hash"]
        ):
            raise JournalIdentityConflict("decision", decision_id)
        return RecordedRouteDecision(
            decision_id, existing.correlation_id, route_input, decision
        )
    correlation_id = (
        f"command.route.{identity}" if decision.disposition.value == "execute" else None
    )
    candidates = [input_candidate, _candidate("proposed", decision.proposed)]
    if decision.actual is not None:
        candidates.append(_candidate("actual", decision.actual))
    record = DecisionRecord(
        decision_id=decision_id,
        kind=ROUTE_DECISION_KIND,
        disposition=decision.disposition,
        decided_at=_now_iso(),
        signals={"refs": list(decision.supporting_signal_refs)},
        candidates=candidates,
        choice=(
            decision.proposed.tier.value
            if decision.proposed.tier is not None
            else decision.action.value
        ),
        reason=decision.reason.value,
        policy_version=decision.policy_version,
        budget_before={"state": "unknown"},
        budget_after={"cap": decision.proposed.budget_cap},
        work_item_id=route_input.work_item_id,
        task_id=route_input.task_id,
        cause_id=route_input.input_fact_refs[0]
        if route_input.input_fact_refs
        else None,
        correlation_id=correlation_id,
    )
    if correlation_id is None:
        store.append_decision(record)
    else:
        intent = EventEnvelope(
            event_id=f"event.route.intent.{identity}",
            kind=EventKind.COMMAND_INTENT,
            occurred_at=_now_iso(),
            source="cognitive_router",
            provenance=Provenance.MACHINE_OBSERVATION,
            policy_version=decision.policy_version,
            payload={
                "command_kind": "route.apply",
                "target_ref": f"work_item.{route_input.work_item_id}",
                "idempotency_key_hash": _hash(idempotency_key),
                "args_hash": input_candidate["input_hash"],
            },
            work_item_id=route_input.work_item_id,
            task_id=route_input.task_id,
            cause_id=decision_id,
            correlation_id=correlation_id,
        )
        store.append_decision_with_intent(record, intent)
    return RecordedRouteDecision(decision_id, correlation_id, route_input, decision)


def _route_record(store, decision_id: str):
    row = next(
        (item for _, item in store.list_decisions() if item.decision_id == decision_id),
        None,
    )
    if row is None or row.kind != ROUTE_DECISION_KIND:
        raise ValueError("unknown route decision")
    return row


def record_route_actual(
    store,
    decision_id: str,
    *,
    episode_id: str,
    model: str | None,
    effort: str | None,
    tier: ModelTier | None,
    evidence_ref: str,
) -> str:
    row = _route_record(store, decision_id)
    event_id = f"event.route.actual.{_token(decision_id)}"
    store.append_event(
        EventEnvelope(
            event_id=event_id,
            kind=ROUTE_ACTUAL_KIND,
            occurred_at=_now_iso(),
            source="episode_runner",
            provenance=Provenance.MACHINE_OBSERVATION,
            policy_version=row.policy_version,
            payload={
                "model": model,
                "effort": effort,
                "tier": tier.value if tier is not None else None,
                "evidence_ref": evidence_ref,
            },
            work_item_id=row.work_item_id,
            task_id=row.task_id,
            episode_id=episode_id,
            cause_id=decision_id,
            correlation_id=row.correlation_id,
        )
    )
    return event_id


def record_route_review(
    store,
    decision_id: str,
    *,
    classification: RouteReviewClass,
    trusted_verifier: bool,
    evidence_refs: tuple[str, ...],
    reviewer_ref: str,
) -> str:
    row = _route_record(store, decision_id)
    if not reviewer_ref.strip() or not evidence_refs:
        raise ValueError("route review requires reviewer and evidence")
    route_actual = next(
        (
            item
            for _, item in store.list_events()
            if item.kind == ROUTE_ACTUAL_KIND and item.cause_id == decision_id
        ),
        None,
    )
    if route_actual is None or route_actual.episode_id is None:
        raise ValueError("route review requires a completed Episode actual")
    terminal = next(
        (
            item
            for _, item in store.list_events()
            if item.episode_id == route_actual.episode_id
            and item.kind == EventKind.COMMAND_RESULT
            and item.payload.get("result_code") == "terminal_observed"
        ),
        None,
    )
    if terminal is None:
        raise ValueError("route review requires a terminal Episode result")
    if trusted_verifier:
        evidence_events = {item.event_id: item for _, item in store.list_events()}
        verifier_versions: set[str] = set()
        for evidence_ref in evidence_refs:
            event = evidence_events.get(evidence_ref)
            if event is None or event.kind != "cognitive_signal.recorded":
                raise ValueError("trusted route review requires validator evidence")
            if (
                event.episode_id != route_actual.episode_id
                or event.task_id != row.task_id
            ):
                raise ValueError(
                    "trusted validator evidence does not belong to the routed Episode"
                )
            signal = read_signal_from_event_payload(event.payload)
            if (
                signal.kind.family is not SignalFamily.VALIDATOR_OUTCOME
                or signal.reliability is not Reliability.RELIABLE
            ):
                raise ValueError("trusted route review requires validator evidence")
            verifier_versions.add(
                f"{signal.normalizer_version}/{signal.comparison_key_version}"
            )
    else:
        verifier_versions = set()
    event_id = f"event.route.review.{_token(decision_id + row.policy_version)}"
    store.append_event(
        EventEnvelope(
            event_id=event_id,
            kind=ROUTE_REVIEW_KIND,
            occurred_at=_now_iso(),
            source="structured_route_review",
            provenance=Provenance.USER_DECISION,
            policy_version=row.policy_version,
            payload={
                "classification": classification.value,
                "trusted_verifier": trusted_verifier,
                "evidence_refs": list(evidence_refs),
                "reviewer_ref_hash": _hash(reviewer_ref),
                "verifier_versions": sorted(verifier_versions),
            },
            work_item_id=row.work_item_id,
            task_id=row.task_id,
            cause_id=decision_id,
            correlation_id=row.correlation_id,
        )
    )
    return event_id


def record_route_approval(store, *, reviewer_ref: str) -> str:
    from trowel_py.model_os.routing.read_model import build_route_gate

    if not reviewer_ref.strip():
        raise ValueError("route approval requires reviewer")
    existing = next(
        (
            item
            for _, item in store.list_events()
            if item.kind == ROUTE_APPROVAL_KIND
            and item.policy_version == "m8-l10-paired-20260723"
        ),
        None,
    )
    if existing is not None:
        raise ValueError("route policy is already approved")
    gate = build_route_gate(store)
    if not gate.ready_for_human_review:
        raise ValueError("route gate is not ready for human approval")
    event_id = f"event.route.approval.{_token(gate.as_of.__repr__())}"
    store.append_event(
        EventEnvelope(
            event_id=event_id,
            kind=ROUTE_APPROVAL_KIND,
            occurred_at=_now_iso(),
            source="structured_route_review",
            provenance=Provenance.USER_DECISION,
            policy_version="m8-l10-paired-20260723",
            payload={
                "reviewer_ref_hash": _hash(reviewer_ref),
                "event_seq": gate.as_of.event_seq,
                "decision_seq": gate.as_of.decision_seq,
            },
        )
    )
    return event_id


def route_tier_for_episode(store, episode_id: str) -> ModelTier | None:
    event = next(
        (
            item
            for _, item in reversed(store.list_events())
            if item.kind == ROUTE_ACTUAL_KIND and item.episode_id == episode_id
        ),
        None,
    )
    if event is None or event.payload.get("tier") is None:
        return None
    return ModelTier(str(event.payload["tier"]))
