from __future__ import annotations

from dataclasses import FrozenInstanceError, replace

import pytest

from trowel_py.model_os.cognitive_signals import (
    CausalHypothesis,
    CausalLink,
    CausalRelation,
    CognitiveSignal,
    DerivedContradiction,
    FailureMode,
    ModelReportPayload,
    PendingLostPayload,
    PendingState,
    Reliability,
    RequiredAction,
    SignalFamily,
    SignalKind,
    StateSample,
    TurnOutcomePayload,
    UserFeedbackPayload,
    ValidatorOutcomePayload,
    build_signal,
    hash_text,
)
from trowel_py.model_os.signal_assessment import (
    EligibilityClass,
    build_route_evidence_bundle,
    classify_route_evidence,
    comparable_failure_series,
    derive_outcome_assessment,
)
from trowel_py.model_os.types import Provenance

from .support import execution_draft


def _signal(draft=None, provenance=Provenance.MACHINE_OBSERVATION) -> CognitiveSignal:
    return build_signal(
        draft or execution_draft(),
        provenance=provenance,
        recorded_at="2026-07-25T01:00:01+00:00",
        policy_version="signal-policy-v1",
    )


def _terminal_failure(draft) -> CognitiveSignal:
    return _signal(
        replace(
            draft,
            native_event_id="terminal-failure",
            kind=SignalKind(SignalFamily.TURN_OUTCOME, "failure"),
            comparison_key=replace(draft.comparison_key, attempt_category="turn:main"),
            payload=TurnOutcomePayload(draft.evidence_refs[0]),
        )
    )


def test_signal_is_frozen_and_has_no_magic_truth_fields() -> None:
    signal = _signal()
    with pytest.raises(FrozenInstanceError):
        signal.signal_id = "changed"  # type: ignore[misc]
    assert "new_evidence" not in signal.__dataclass_fields__
    assert {item.value for item in Reliability} == {"reliable", "weak"}
    assert "model_limited" not in {item.value for item in CausalHypothesis}


def test_pending_lost_payload_and_subtype_are_bidirectionally_bound() -> None:
    draft = execution_draft()
    pending = PendingLostPayload(
        resolution_state="requires_reconcile",
        effect_certainty="unknown",
        required_action="inspect_reality",
    )
    legal = replace(
        draft,
        kind=SignalKind(SignalFamily.EXECUTION_OBSERVATION, "pending_lost"),
        validity=PendingState(),
        payload=pending,
    )
    assert _signal(legal).payload is pending
    with pytest.raises(ValueError, match="PendingLostPayload"):
        _signal(replace(legal, payload=draft.payload))
    with pytest.raises(ValueError, match="pending_lost"):
        _signal(replace(draft, payload=pending))


def test_single_validator_failure_never_invents_model_incapacity() -> None:
    draft = execution_draft()
    ref = draft.evidence_refs[0]
    validator = replace(
        draft,
        kind=SignalKind(SignalFamily.VALIDATOR_OUTCOME, "fail"),
        comparison_key=replace(
            draft.comparison_key,
            attempt_category="validator:pytest",
            validator_intent_id="pytest",
        ),
        payload=ValidatorOutcomePayload(
            intent_id="pytest",
            native_tool_item_id=draft.attempt_id,
            normalized_argv=("pytest", "tests/example.py"),
            exit_event_ref=ref,
            output_ref=None,
            exit_code=1,
            completed=True,
        ),
    )
    signal = _signal(validator)
    outcome, contradictions = derive_outcome_assessment(
        (signal,), terminal=None, as_of="2026-07-25T02:00:00+00:00",
        attribution_policy_version="attribution-v1",
    )
    assert outcome.failure_mode is FailureMode.VALIDATION_FAILED
    assert outcome.causal_hypothesis is CausalHypothesis.UNKNOWN
    assert outcome.required_action is RequiredAction.VERIFY
    assert contradictions == ()


def test_environment_and_validator_coexist_as_mixed_without_losing_refs() -> None:
    draft = execution_draft()
    execution = _signal(draft)
    terminal = _terminal_failure(
        replace(
            draft,
            causal_parent_ref=CausalLink(CausalRelation.DIRECT, execution.signal_id),
        )
    )
    draft = execution_draft(native_event_id="validator-event")
    ref = draft.evidence_refs[0]
    validator = _signal(
        replace(
            draft,
            kind=SignalKind(SignalFamily.VALIDATOR_OUTCOME, "fail"),
            comparison_key=replace(
                draft.comparison_key,
                attempt_category="validator:pytest",
                validator_intent_id="pytest",
            ),
            payload=ValidatorOutcomePayload(
                intent_id="pytest",
                native_tool_item_id=draft.attempt_id,
                normalized_argv=("pytest",),
                exit_event_ref=ref,
                output_ref=None,
                exit_code=1,
                completed=True,
            ),
        )
    )
    outcome, _ = derive_outcome_assessment(
        (execution, validator), terminal=terminal,
        as_of="2026-07-25T02:00:00+00:00",
        attribution_policy_version="attribution-v1",
    )
    assert outcome.causal_hypothesis is CausalHypothesis.MIXED
    assert set(outcome.supporting_signal_ids) == {
        execution.signal_id,
        validator.signal_id,
        terminal.signal_id,
    }


def test_user_rejection_wins_failure_mode_while_environment_keeps_mixed_cause() -> None:
    draft = execution_draft()
    execution = _signal(draft)
    terminal = _terminal_failure(
        replace(
            draft,
            causal_parent_ref=CausalLink(CausalRelation.DIRECT, execution.signal_id),
        )
    )
    draft = execution_draft(native_event_id="user-action-1")
    user = _signal(
        replace(
            draft,
            kind=SignalKind(SignalFamily.USER_FEEDBACK, "correction"),
            payload=UserFeedbackPayload(draft.evidence_refs[0]),
        ),
        provenance=Provenance.USER_DECISION,
    )
    outcome, _ = derive_outcome_assessment(
        (execution, user), terminal=terminal,
        as_of="2026-07-25T02:00:00+00:00",
        attribution_policy_version="attribution-v1",
    )
    assert outcome.failure_mode is FailureMode.USER_REJECTED
    assert outcome.causal_hypothesis is CausalHypothesis.MIXED


def test_model_report_is_advisory_and_cannot_make_itself_eligible() -> None:
    draft = execution_draft(native_event_id="message-1")
    report = _signal(
        replace(
            draft,
            kind=SignalKind(SignalFamily.MODEL_REPORT, "uncertainty"),
            reliability=Reliability.WEAK,
            payload=ModelReportPayload(
                native_message_ref=draft.evidence_refs[0],
                text_hash=hash_text("model-report"),
            ),
        ),
        provenance=Provenance.MODEL_HYPOTHESIS,
    )
    assert classify_route_evidence(report) is EligibilityClass.ADVISORY
    bundle = build_route_evidence_bundle(
        (report,), policy_version="route-evidence-v1",
        actual_outcome=None, negative_sample_marker=True,
    )
    assert bundle.eligible_trigger_ids == ()
    assert bundle.advisory_ids == (report.signal_id,)
    assert bundle.supporting_signal_refs == (report.signal_id,)
    assert bundle.negative_sample_marker is True


def test_comparable_failures_do_not_cross_runtime_target_or_category() -> None:
    anchor = _signal()
    same = _signal(execution_draft(attempt_id="attempt-2", native_event_id="event-2"))
    other_runtime = _signal(
        execution_draft(
            attempt_id="attempt-3", runtime="codex", native_event_id="event-3"
        )
    )
    other_category = _signal(
        replace(
            execution_draft(attempt_id="attempt-4", native_event_id="event-4"),
            comparison_key=replace(
                anchor.comparison_key, attempt_category="turn:main"
            ),
        )
    )
    assert comparable_failure_series(
        anchor, (anchor, same, other_runtime, other_category)
    ) == ("attempt-1", "attempt-2")


def test_comparable_failures_and_bundle_do_not_mix_policy_versions() -> None:
    anchor = _signal()
    incompatible = _signal(
        replace(
            execution_draft(attempt_id="attempt-2", native_event_id="event-2"),
            comparison_key_version="comparison-v999",
        )
    )
    assert comparable_failure_series(anchor, (anchor, incompatible)) == ("attempt-1",)
    bundle = build_route_evidence_bundle(
        (anchor, incompatible),
        policy_version="route-v1",
        actual_outcome=None,
        negative_sample_marker=False,
    )
    assert incompatible.signal_id in bundle.excluded_ids


def test_contradiction_is_canonical_and_read_only() -> None:
    draft = execution_draft()
    ref = draft.evidence_refs[0]
    success = _signal(
        replace(
            draft,
            native_event_id="turn-success",
            kind=SignalKind(SignalFamily.TURN_OUTCOME, "success"),
            comparison_key=replace(draft.comparison_key, attempt_category="turn:main"),
            payload=TurnOutcomePayload(terminal_event_ref=ref),
        )
    )
    validator = _signal(
        replace(
            draft,
            native_event_id="validator-fail",
            kind=SignalKind(SignalFamily.VALIDATOR_OUTCOME, "fail"),
            comparison_key=replace(
                draft.comparison_key,
                attempt_category="validator:pytest",
                validator_intent_id="pytest",
            ),
            payload=ValidatorOutcomePayload(
                intent_id="pytest",
                native_tool_item_id=draft.attempt_id,
                normalized_argv=("pytest",),
                exit_event_ref=ref,
                output_ref=None,
                exit_code=1,
                completed=True,
            ),
        )
    )
    inputs = (success, validator)
    _, contradictions = derive_outcome_assessment(
        inputs, terminal=success, as_of="2026-07-25T02:00:00+00:00",
        attribution_policy_version="attribution-v1",
    )
    assert inputs == (success, validator)
    assert len(contradictions) == 1
    contradiction: DerivedContradiction = contradictions[0]
    assert contradiction.left_signal_id < contradiction.right_signal_id


def test_separate_terminal_is_supporting_evidence_and_detects_contradiction() -> None:
    draft = execution_draft()
    ref = draft.evidence_refs[0]
    terminal = _signal(
        replace(
            draft,
            native_event_id="terminal-only",
            kind=SignalKind(SignalFamily.TURN_OUTCOME, "success"),
            comparison_key=replace(draft.comparison_key, attempt_category="turn:main"),
            payload=TurnOutcomePayload(ref),
        )
    )
    validator = _signal(
        replace(
            draft,
            native_event_id="validator-only",
            kind=SignalKind(SignalFamily.VALIDATOR_OUTCOME, "fail"),
            comparison_key=replace(
                draft.comparison_key,
                attempt_category="validator:pytest",
                validator_intent_id="pytest",
            ),
            payload=ValidatorOutcomePayload(
                "pytest", draft.attempt_id, ("pytest",), ref, None, 1, True
            ),
        )
    )
    outcome, contradictions = derive_outcome_assessment(
        (validator,), terminal=terminal,
        as_of="2026-07-25T02:00:00+00:00",
        attribution_policy_version="attribution-v1",
    )
    assert set(outcome.supporting_signal_ids) == {
        terminal.signal_id,
        validator.signal_id,
    }
    assert len(contradictions) == 1


def test_z_and_offset_utc_formats_compare_by_time_not_text() -> None:
    signal = _signal(
        replace(
            execution_draft(),
            validity=StateSample(valid_until="2026-07-25T02:00:00Z"),
        )
    )
    outcome, _ = derive_outcome_assessment(
        (signal,), terminal=None,
        as_of="2026-07-25T01:30:00+00:00",
        attribution_policy_version="attribution-v1",
    )
    assert signal.signal_id in outcome.supporting_signal_ids


def test_unlinked_network_observation_does_not_invent_environment_cause() -> None:
    outcome, _ = derive_outcome_assessment(
        (_signal(),), terminal=None,
        as_of="2026-07-25T02:00:00+00:00",
        attribution_policy_version="attribution-v1",
    )
    assert outcome.failure_mode is FailureMode.EXECUTION_FAILED
    assert outcome.causal_hypothesis is CausalHypothesis.UNKNOWN


@pytest.mark.parametrize("child_kind", ["model", "retry", "user"])
def test_non_terminal_child_cannot_upgrade_network_cause(child_kind: str) -> None:
    network = _signal()
    base = execution_draft(native_event_id=f"{child_kind}-child")
    link = CausalLink(CausalRelation.DIRECT, network.signal_id)
    if child_kind == "model":
        child = _signal(
            replace(
                base,
                kind=SignalKind(SignalFamily.MODEL_REPORT, "uncertainty"),
                reliability=Reliability.WEAK,
                payload=ModelReportPayload(
                    base.evidence_refs[0], hash_text("uncertain")
                ),
                causal_parent_ref=link,
            ),
            provenance=Provenance.MODEL_HYPOTHESIS,
        )
    elif child_kind == "user":
        child = _signal(
            replace(
                base,
                kind=SignalKind(SignalFamily.USER_FEEDBACK, "correction"),
                payload=UserFeedbackPayload(base.evidence_refs[0]),
                causal_parent_ref=link,
            ),
            provenance=Provenance.USER_DECISION,
        )
    else:
        child = _signal(
            replace(
                base,
                kind=SignalKind(SignalFamily.EXECUTION_OBSERVATION, "retry"),
                causal_parent_ref=link,
            )
        )
    outcome, _ = derive_outcome_assessment(
        (network, child),
        terminal=None,
        as_of="2026-07-25T02:00:00+00:00",
        attribution_policy_version="attribution-v1",
    )
    assert outcome.causal_hypothesis is CausalHypothesis.UNKNOWN


def test_retry_observation_does_not_turn_success_into_failure() -> None:
    draft = execution_draft()
    retry = _signal(
        replace(
            draft,
            kind=SignalKind(SignalFamily.EXECUTION_OBSERVATION, "retry"),
        )
    )
    success = _signal(
        replace(
            draft,
            native_event_id="turn-success-after-retry",
            kind=SignalKind(SignalFamily.TURN_OUTCOME, "success"),
            comparison_key=replace(draft.comparison_key, attempt_category="turn:main"),
            payload=TurnOutcomePayload(draft.evidence_refs[0]),
        )
    )
    outcome, _ = derive_outcome_assessment(
        (retry,), terminal=success,
        as_of="2026-07-25T02:00:00+00:00",
        attribution_policy_version="attribution-v1",
    )
    assert outcome.observed_outcome == "success"
    assert outcome.failure_mode is FailureMode.UNKNOWN
