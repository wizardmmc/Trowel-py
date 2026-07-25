"""不访问外部状态的结果归因与路由证据分类。"""

from __future__ import annotations

from datetime import datetime
from enum import Enum

from trowel_py.model_os.cognitive_signals import (
    CausalHypothesis,
    CausalRelation,
    CognitiveSignal,
    DerivedContradiction,
    ExecutionOutcomePayload,
    FailureMode,
    OutcomeAttribution,
    PendingLostPayload,
    PendingState,
    Reliability,
    RequiredAction,
    RouteEvidenceBundle,
    SignalFamily,
    StateSample,
    ToolFailureCategory,
)
from trowel_py.model_os.types import Provenance


class EligibilityClass(str, Enum):
    ELIGIBLE_TRIGGER = "eligible_trigger"
    ADVISORY = "advisory"
    EXCLUDED = "excluded"


_ENVIRONMENTAL = frozenset(
    {
        ToolFailureCategory.NETWORK,
        ToolFailureCategory.PERMISSION,
        ToolFailureCategory.RATE_LIMIT,
        ToolFailureCategory.RESOURCE,
    }
)

_FAILURE_KINDS = frozenset(
    {
        (SignalFamily.VALIDATOR_OUTCOME, "fail"),
        (SignalFamily.TURN_OUTCOME, "failure"),
        (SignalFamily.TURN_OUTCOME, "partial"),
        (SignalFamily.EXECUTION_OBSERVATION, "tool_error"),
        (SignalFamily.EXECUTION_OBSERVATION, "runtime_error"),
        (SignalFamily.EXECUTION_OBSERVATION, "timeout"),
    }
)


def classify_route_evidence(signal: CognitiveSignal) -> EligibilityClass:
    family = signal.kind.family
    subtype = signal.kind.subtype
    if (
        family is SignalFamily.USER_FEEDBACK
        and subtype == "deep_override"
        and signal.provenance is Provenance.USER_DECISION
        and signal.reliability is Reliability.RELIABLE
    ):
        return EligibilityClass.ELIGIBLE_TRIGGER
    if family is SignalFamily.VALIDATOR_OUTCOME and subtype == "fail":
        if (
            signal.provenance is Provenance.MACHINE_OBSERVATION
            and signal.reliability is Reliability.RELIABLE
        ):
            return EligibilityClass.ELIGIBLE_TRIGGER
        return EligibilityClass.ADVISORY
    if family is SignalFamily.EXECUTION_OBSERVATION:
        if subtype == "pending_lost":
            return EligibilityClass.EXCLUDED
        if isinstance(signal.payload, ExecutionOutcomePayload):
            if signal.payload.category in _ENVIRONMENTAL:
                return EligibilityClass.EXCLUDED
        return EligibilityClass.ADVISORY
    return EligibilityClass.ADVISORY


def build_route_evidence_bundle(
    signals: tuple[CognitiveSignal, ...],
    *,
    policy_version: str,
    actual_outcome: OutcomeAttribution | None,
    negative_sample_marker: bool,
    compatible_signal_versions: frozenset[tuple[str, str]] | None = None,
) -> RouteEvidenceBundle:
    eligible: list[str] = []
    advisory: list[str] = []
    excluded: list[str] = []
    compatible = compatible_signal_versions
    if compatible is None and signals:
        first = signals[0]
        compatible = frozenset(
            {(first.normalizer_version, first.comparison_key_version)}
        )
    for signal in signals:
        if compatible is not None and (
            signal.normalizer_version,
            signal.comparison_key_version,
        ) not in compatible:
            excluded.append(signal.signal_id)
            continue
        classification = classify_route_evidence(signal)
        if classification is EligibilityClass.ELIGIBLE_TRIGGER:
            eligible.append(signal.signal_id)
        elif classification is EligibilityClass.EXCLUDED:
            excluded.append(signal.signal_id)
        else:
            advisory.append(signal.signal_id)
    return RouteEvidenceBundle(
        eligible_trigger_ids=tuple(eligible),
        advisory_ids=tuple(advisory),
        excluded_ids=tuple(excluded),
        supporting_signal_refs=tuple(signal.signal_id for signal in signals),
        actual_outcome=actual_outcome,
        negative_sample_marker=negative_sample_marker,
        policy_version=policy_version,
    )


def comparable_failure_series(
    anchor: CognitiveSignal,
    signals: tuple[CognitiveSignal, ...],
) -> tuple[str, ...]:
    anchor_hash = anchor.comparison_key.stable_hash()
    attempt_ids: list[str] = []
    for signal in signals:
        if (
            signal.normalizer_version != anchor.normalizer_version
            or signal.comparison_key_version != anchor.comparison_key_version
        ):
            continue
        if signal.comparison_key.stable_hash() != anchor_hash:
            continue
        if (signal.kind.family, signal.kind.subtype) not in _FAILURE_KINDS:
            continue
        if signal.attempt_id not in attempt_ids:
            attempt_ids.append(signal.attempt_id)
    return tuple(attempt_ids)


def _is_expired(signal: CognitiveSignal, as_of: str) -> bool:
    if isinstance(signal.validity, StateSample):
        return datetime.fromisoformat(signal.validity.valid_until) < datetime.fromisoformat(
            as_of
        )
    return isinstance(signal.validity, PendingState) and (
        signal.validity.terminal_event_ref is not None
    )


def derive_outcome_assessment(
    attempt_signals: tuple[CognitiveSignal, ...],
    terminal: CognitiveSignal | None,
    *,
    as_of: str,
    attribution_policy_version: str,
    detector_version: str = "contradiction-v1",
) -> tuple[OutcomeAttribution, tuple[DerivedContradiction, ...]]:
    attempt_id = (
        terminal.attempt_id
        if terminal is not None
        else (attempt_signals[0].attempt_id if attempt_signals else "unknown")
    )
    combined = attempt_signals
    if terminal is not None and all(
        signal.signal_id != terminal.signal_id for signal in combined
    ):
        combined += (terminal,)
    active = tuple(
        signal
        for signal in combined
        if signal.attempt_id == attempt_id and not _is_expired(signal, as_of)
    )
    excluded = tuple(signal for signal in combined if signal not in active)

    pending = [
        signal
        for signal in active
        if signal.kind.family is SignalFamily.EXECUTION_OBSERVATION
        and signal.kind.subtype == "pending_lost"
    ]
    validator_fails = [
        signal
        for signal in active
        if signal.kind.family is SignalFamily.VALIDATOR_OUTCOME
        and signal.kind.subtype == "fail"
        and signal.reliability is Reliability.RELIABLE
    ]
    user_rejections = [
        signal
        for signal in active
        if signal.kind.family is SignalFamily.USER_FEEDBACK
        and signal.kind.subtype == "correction"
        and signal.provenance is Provenance.USER_DECISION
        and signal.reliability is Reliability.RELIABLE
    ]
    environmental: list[CognitiveSignal] = []
    unlinked_environmental: list[CognitiveSignal] = []
    other_execution: list[CognitiveSignal] = []
    directly_linked_causes = {
        signal.causal_parent_ref.parent_signal_id
        for signal in active
        if signal.causal_parent_ref is not None
        and signal.causal_parent_ref.relation is CausalRelation.DIRECT
        and signal.kind.family is SignalFamily.TURN_OUTCOME
        and signal.kind.subtype in {"failure", "partial"}
        and signal.provenance is Provenance.MACHINE_OBSERVATION
        and signal.reliability is Reliability.RELIABLE
    }
    for signal in active:
        if signal.kind.family is not SignalFamily.EXECUTION_OBSERVATION:
            continue
        if signal.kind.subtype in {"pending_lost", "retry"}:
            continue
        if isinstance(signal.payload, ExecutionOutcomePayload):
            if signal.payload.category in _ENVIRONMENTAL:
                target = (
                    environmental
                    if signal.signal_id in directly_linked_causes
                    else unlinked_environmental
                )
            else:
                target = other_execution
            target.append(signal)

    cause_buckets: set[str] = set()
    if pending or environmental:
        cause_buckets.add("environment")
    if validator_fails or user_rejections or other_execution or unlinked_environmental:
        cause_buckets.add("unknown")
    if cause_buckets == {"environment"}:
        causal = CausalHypothesis.ENVIRONMENT
    elif len(cause_buckets) > 1:
        causal = CausalHypothesis.MIXED
    else:
        causal = CausalHypothesis.UNKNOWN

    if pending:
        failure_mode = FailureMode.PENDING_LOST
        payload = pending[0].payload
        assert isinstance(payload, PendingLostPayload)
        required = (
            RequiredAction.ASK_USER_AGAIN
            if payload.required_action == "ask_user_again"
            else RequiredAction.RECONCILE
        )
    elif user_rejections:
        failure_mode = FailureMode.USER_REJECTED
        required = RequiredAction.VERIFY
    elif validator_fails and not environmental:
        failure_mode = FailureMode.VALIDATION_FAILED
        required = RequiredAction.VERIFY
    elif environmental or unlinked_environmental or other_execution:
        failure_mode = FailureMode.EXECUTION_FAILED
        required = (
            RequiredAction.RETRY
            if environmental or unlinked_environmental
            else RequiredAction.VERIFY
        )
    else:
        failure_mode = FailureMode.UNKNOWN
        required = RequiredAction.NONE

    observed_outcome = (
        terminal.kind.subtype
        if terminal is not None
        and terminal.kind.family is SignalFamily.TURN_OUTCOME
        and terminal.attempt_id == attempt_id
        else "unknown"
    )
    attribution = OutcomeAttribution(
        attempt_id=attempt_id,
        observed_outcome=observed_outcome,
        failure_mode=failure_mode,
        causal_hypothesis=causal,
        required_action=required,
        supporting_signal_ids=tuple(signal.signal_id for signal in active),
        excluded_signal_ids=tuple(signal.signal_id for signal in excluded),
        attribution_policy_version=attribution_policy_version,
    )

    successes = [
        signal
        for signal in active
        if signal.kind.family is SignalFamily.TURN_OUTCOME
        and signal.kind.subtype == "success"
    ]
    contradictions: list[DerivedContradiction] = []
    for success in successes:
        for conflicting in (*validator_fails, *user_rejections):
            left, right = sorted((success.signal_id, conflicting.signal_id))
            pair_hash = f"{left}\x1f{right}\x1fclaim_vs_evidence\x1f{detector_version}"
            contradictions.append(
                DerivedContradiction(
                    contradiction_id="contradiction."
                    + __import__("hashlib").sha256(pair_hash.encode()).hexdigest(),
                    left_signal_id=left,
                    right_signal_id=right,
                    relation="claim_vs_evidence",
                    strength=Reliability.RELIABLE,
                    detector_version=detector_version,
                )
            )
    return attribution, tuple(contradictions)
