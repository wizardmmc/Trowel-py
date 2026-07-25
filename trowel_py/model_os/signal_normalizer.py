"""把 runtime 的原子观察归一化为认知信号草稿。"""

from __future__ import annotations

from dataclasses import replace
from typing import Any, Mapping

from trowel_py.model_os.cognitive_signals import (
    CognitiveSignalDraft,
    ExecutionOutcomePayload,
    PendingLostPayload,
    PendingState,
    PointFact,
    Reliability,
    SignalFamily,
    SignalKind,
    SignalNormalizationContext,
    ToolFailureCategory,
    TurnOutcomePayload,
)


def normalize_and_classify(
    raw_observation: Mapping[str, Any],
    context: SignalNormalizationContext,
) -> CognitiveSignalDraft | None:
    """消费单条原子观察；聚合 fixture 必须先由 adapter 拆回原事件。"""

    runtime = context.comparison_key.runtime
    observation_type = raw_observation.get("type")
    method = raw_observation.get("method")

    if observation_type == "result":
        failed = bool(raw_observation.get("is_error")) or (
            raw_observation.get("subtype") != "success"
        )
        return _turn_draft(raw_observation, context, failed=failed)
    if method == "turn/completed":
        failed = raw_observation.get("status") != "completed" or (
            raw_observation.get("error") is not None
        )
        return _turn_draft(raw_observation, context, failed=failed)
    if observation_type == "system" and raw_observation.get("subtype") == "api_retry":
        return _execution_draft(
            context,
            subtype="retry",
            category=ToolFailureCategory.NETWORK,
            reliability=Reliability.RELIABLE,
            retry_attempt=_optional_int(raw_observation.get("attempt")),
            retry_max=_optional_int(raw_observation.get("max_retries")),
            retry_delay_ms=_optional_float(raw_observation.get("retry_delay_ms")),
        )
    if method == "error" and raw_observation.get("will_retry") is True:
        return _execution_draft(
            context,
            subtype="retry",
            category=ToolFailureCategory.NETWORK,
            reliability=Reliability.WEAK,
        )
    if observation_type == "tool_result":
        message = str(raw_observation.get("message", "")).lower()
        if "timed out" in message or raw_observation.get("timed_out") is True:
            return _execution_draft(
                context,
                subtype="timeout",
                category=ToolFailureCategory.RESOURCE,
                reliability=Reliability.RELIABLE,
            )
        if raw_observation.get("is_error") is True:
            return _execution_draft(
                context,
                subtype="tool_error",
                category=ToolFailureCategory.UNKNOWN,
                reliability=Reliability.RELIABLE,
            )
        return None
    if observation_type == "commandExecution" and raw_observation.get("status") == "failed":
        return _execution_draft(
            context,
            subtype="tool_error",
            category=ToolFailureCategory.UNKNOWN,
            reliability=Reliability.RELIABLE,
        )
    if observation_type == "control_request" or method in {
        "item/commandExecution/requestApproval",
        "item/fileChange/requestApproval",
    }:
        return _execution_draft(
            context,
            subtype="tool_error",
            category=ToolFailureCategory.PERMISSION,
            reliability=Reliability.RELIABLE,
        )
    if observation_type == "pending_lost":
        disposition = context.pending_disposition or (
            str(raw_observation.get("resolution_state", "requires_reconcile")),
            str(raw_observation.get("effect_certainty", "unknown")),
            str(raw_observation.get("required_action", "inspect_reality")),
        )
        return CognitiveSignalDraft(
            native_event_id=context.native_event_id,
            observation_ordinal=context.observation_ordinal,
            kind=SignalKind(SignalFamily.EXECUTION_OBSERVATION, "pending_lost"),
            reliability=Reliability.RELIABLE,
            validity=PendingState(),
            subject=context.subject,
            attempt_id=context.attempt_id,
            comparison_key=replace(
                context.comparison_key,
                attempt_category="execution:pending_lost",
            ),
            payload=PendingLostPayload(
                *disposition,
                native_binding_generation=context.binding_generation,
            ),
            evidence_refs=context.evidence_refs,
            observed_at=context.observed_at,
            normalizer_version=context.normalizer_version,
            comparison_key_version=context.comparison_key_version,
        )
    if raw_observation.get("event_type") == "error" and (
        raw_observation.get("will_retry") is False
    ):
        return _turn_draft(raw_observation, context, failed=True)
    if observation_type == "native_error" and runtime in {"cc", "codex"}:
        return _turn_draft(raw_observation, context, failed=True)
    return None


def _turn_draft(
    raw: Mapping[str, Any],
    context: SignalNormalizationContext,
    *,
    failed: bool,
) -> CognitiveSignalDraft:
    runtime = context.comparison_key.runtime
    subtype = "failure" if failed else "success"
    return CognitiveSignalDraft(
        native_event_id=context.native_event_id,
        observation_ordinal=context.observation_ordinal,
        kind=SignalKind(SignalFamily.TURN_OUTCOME, subtype),
        reliability=Reliability.RELIABLE,
        validity=PointFact(),
        subject=context.subject,
        attempt_id=context.attempt_id,
        comparison_key=replace(
            context.comparison_key, attempt_category="turn:main"
        ),
        payload=TurnOutcomePayload(
            terminal_event_ref=context.evidence_refs[0],
            requested_effort=_optional_str(raw.get("requested_effort")),
            effective_effort=(
                None
                if runtime == "cc"
                else _optional_str(raw.get("effective_effort"))
            ),
        ),
        evidence_refs=context.evidence_refs,
        observed_at=context.observed_at,
        normalizer_version=context.normalizer_version,
        comparison_key_version=context.comparison_key_version,
    )


def _execution_draft(
    context: SignalNormalizationContext,
    *,
    subtype: str,
    category: ToolFailureCategory,
    reliability: Reliability,
    retry_attempt: int | None = None,
    retry_max: int | None = None,
    retry_delay_ms: float | None = None,
) -> CognitiveSignalDraft:
    return CognitiveSignalDraft(
        native_event_id=context.native_event_id,
        observation_ordinal=context.observation_ordinal,
        kind=SignalKind(SignalFamily.EXECUTION_OBSERVATION, subtype),
        reliability=reliability,
        validity=PointFact(),
        subject=context.subject,
        attempt_id=context.attempt_id,
        comparison_key=replace(
            context.comparison_key, attempt_category=f"execution:{subtype}"
        ),
        payload=ExecutionOutcomePayload(
            category=category,
            native_item_ref=context.evidence_refs[0],
            retry_attempt=retry_attempt,
            retry_max=retry_max,
            retry_delay_ms=retry_delay_ms,
        ),
        evidence_refs=context.evidence_refs,
        observed_at=context.observed_at,
        normalizer_version=context.normalizer_version,
        comparison_key_version=context.comparison_key_version,
    )


def _optional_str(value: Any) -> str | None:
    return str(value) if value is not None else None


def _optional_int(value: Any) -> int | None:
    return int(value) if value is not None else None


def _optional_float(value: Any) -> float | None:
    return float(value) if value is not None else None
