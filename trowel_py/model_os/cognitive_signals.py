"""Model OS 认知信号的值对象与严格编解码。"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Mapping, Protocol

from trowel_py.model_os.types import Provenance


class Reliability(str, Enum):
    RELIABLE = "reliable"
    WEAK = "weak"


class SignalFamily(str, Enum):
    VALIDATOR_OUTCOME = "validator_outcome"
    EXECUTION_OBSERVATION = "execution_observation"
    TURN_OUTCOME = "turn_outcome"
    USER_FEEDBACK = "user_feedback"
    MODEL_REPORT = "model_report"


_LEGAL_SUBTYPES: dict[SignalFamily, frozenset[str]] = {
    SignalFamily.VALIDATOR_OUTCOME: frozenset({"pass", "fail"}),
    SignalFamily.EXECUTION_OBSERVATION: frozenset(
        {"tool_error", "runtime_error", "retry", "timeout", "pending_lost"}
    ),
    SignalFamily.TURN_OUTCOME: frozenset({"success", "failure", "partial"}),
    SignalFamily.USER_FEEDBACK: frozenset(
        {"request", "correction", "deep_override"}
    ),
    SignalFamily.MODEL_REPORT: frozenset({"uncertainty", "yield_proposal"}),
}


@dataclass(frozen=True)
class SignalKind:
    family: SignalFamily
    subtype: str

    def __post_init__(self) -> None:
        legal = _LEGAL_SUBTYPES.get(self.family)
        if legal is None or self.subtype not in legal:
            raise ValueError(
                f"subtype {self.subtype!r} is not legal for family "
                f"{self.family.value!r}"
            )

    @property
    def identifier(self) -> str:
        return f"signal.{self.family.value}/{self.subtype}"

    def __str__(self) -> str:
        return self.identifier


@dataclass(frozen=True)
class PointFact:
    pass


@dataclass(frozen=True)
class StateSample:
    valid_until: str

    def __post_init__(self) -> None:
        _validate_utc_iso(self.valid_until, "StateSample.valid_until")


@dataclass(frozen=True)
class PendingState:
    terminal_event_ref: str | None = None


SignalValidity = PointFact | StateSample | PendingState


@dataclass(frozen=True)
class AttemptRef:
    attempt_id: str

    def __post_init__(self) -> None:
        _validate_structural_label(self.attempt_id, "AttemptRef.attempt_id")


@dataclass(frozen=True)
class EpisodeRef:
    episode_id: str

    def __post_init__(self) -> None:
        _validate_structural_label(self.episode_id, "EpisodeRef.episode_id")


@dataclass(frozen=True)
class TaskRef:
    task_id: str

    def __post_init__(self) -> None:
        _validate_structural_label(self.task_id, "TaskRef.task_id")


SignalSubject = AttemptRef | EpisodeRef | TaskRef


@dataclass(frozen=True)
class EvidenceRef:
    namespace: str
    ref_id: str
    runtime: str | None = None
    event_kind: str | None = None
    content_hash: str | None = None
    invocation_id: str | None = None

    def __post_init__(self) -> None:
        if not self.namespace or not self.ref_id:
            raise ValueError("EvidenceRef requires namespace and ref_id")
        _validate_structural_label(self.namespace, "EvidenceRef.namespace")
        if self.runtime is not None:
            _validate_structural_label(self.runtime, "EvidenceRef.runtime")
        if self.event_kind is not None:
            _validate_structural_label(self.event_kind, "EvidenceRef.event_kind")
        if self.content_hash is not None:
            _validate_sha256(self.content_hash, "EvidenceRef.content_hash")


class EvidenceAuthority(str, Enum):
    RUNTIME_OBSERVATION = "runtime_observation"
    VALIDATOR_EXIT = "validator_exit"
    VALIDATOR_OUTPUT = "validator_output"
    STRUCTURED_USER_ACTION = "structured_user_action"
    NATIVE_MODEL_MESSAGE = "native_model_message"
    JOURNAL_EVENT = "journal_event"


@dataclass(frozen=True)
class RegisteredEvidence:
    reference: EvidenceRef
    authority: EvidenceAuthority
    attempt_id: str
    runtime: str
    task_id: str | None
    episode_id: str | None
    action_subtype: str | None = None


@dataclass(frozen=True)
class ValidatorInvocationEvidence:
    native_tool_item_id: str
    intent_id: str
    attempt_id: str
    runtime: str
    normalized_argv: tuple[str, ...]
    exit_event_ref: EvidenceRef
    output_ref: EvidenceRef | None
    exit_code: int
    completed: bool


class CausalRelation(str, Enum):
    DIRECT = "direct"
    CORRELATED = "correlated"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class CausalLink:
    relation: CausalRelation
    parent_signal_id: str


class ToolFailureCategory(str, Enum):
    NETWORK = "network"
    PERMISSION = "permission"
    INPUT = "input"
    RESOURCE = "resource"
    RATE_LIMIT = "rate_limit"
    MODEL = "model"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class AttemptComparisonKey:
    runtime: str
    attempt_category: str
    task_id: str | None
    target_ref: str | None
    validator_intent_id: str | None = None

    def __post_init__(self) -> None:
        _validate_structural_label(self.runtime, "AttemptComparisonKey.runtime")
        if re.fullmatch(
            r"(validator|tool|turn|execution):[a-z0-9_.-]+",
            self.attempt_category,
        ) is None:
            raise ValueError("comparison key requires runtime and normalized category")
        if self.validator_intent_id is not None and (
            self.attempt_category != f"validator:{self.validator_intent_id}"
        ):
            raise ValueError("validator intent and comparison category must match")
        if self.attempt_category.startswith("validator:") and (
            self.validator_intent_id is None
        ):
            raise ValueError("validator comparison key requires validator_intent_id")
        if self.target_ref is not None and (
            len(self.target_ref) > 512 or "\n" in self.target_ref or "\r" in self.target_ref
        ):
            raise ValueError("target_ref must be a short structural reference")
        if self.task_id is not None:
            _validate_structural_label(self.task_id, "AttemptComparisonKey.task_id")

    def stable_hash(self) -> str:
        raw = json.dumps(
            {
                "runtime": self.runtime,
                "attempt_category": self.attempt_category,
                "task_id": self.task_id,
                "target_ref": self.target_ref,
                "validator_intent_id": self.validator_intent_id,
            },
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(raw.encode()).hexdigest()


@dataclass(frozen=True)
class ValidatorIntent:
    intent_id: str
    name: str
    executable: str
    allowed_flags: tuple[str, ...] = ()
    target_rule: str = ""
    domain: str = "coding"

    def __post_init__(self) -> None:
        for field, value in (
            ("intent_id", self.intent_id),
            ("name", self.name),
            ("executable", self.executable),
            ("target_rule", self.target_rule),
            ("domain", self.domain),
        ):
            _validate_structural_label(value, f"ValidatorIntent.{field}")


@dataclass(frozen=True)
class ValidatorOutcomePayload:
    intent_id: str
    native_tool_item_id: str
    normalized_argv: tuple[str, ...]
    exit_event_ref: EvidenceRef
    output_ref: EvidenceRef | None
    exit_code: int
    completed: bool

    def __post_init__(self) -> None:
        if not self.completed:
            raise ValueError("validator outcome requires a completed invocation")


@dataclass(frozen=True)
class ExecutionOutcomePayload:
    category: ToolFailureCategory
    native_item_ref: EvidenceRef | None = None
    retry_attempt: int | None = None
    retry_max: int | None = None
    retry_delay_ms: float | None = None


@dataclass(frozen=True)
class PendingLostPayload:
    resolution_state: str
    effect_certainty: str
    required_action: str
    native_binding_generation: str | None = None
    terminal_event_ref: EvidenceRef | None = None

    def __post_init__(self) -> None:
        triple = (
            self.resolution_state,
            self.effect_certainty,
            self.required_action,
        )
        legal = {
            ("requires_user_restart", "not_sent", "ask_user_again"),
            ("requires_reconcile", "unknown", "inspect_reality"),
        }
        if triple not in legal:
            raise ValueError(f"invalid pending_lost disposition: {triple!r}")


@dataclass(frozen=True)
class TurnOutcomePayload:
    terminal_event_ref: EvidenceRef
    requested_effort: str | None = None
    effective_effort: str | None = None

    def __post_init__(self) -> None:
        legal_efforts = {"minimal", "low", "medium", "high", "xhigh"}
        if self.requested_effort is not None and self.requested_effort not in legal_efforts:
            raise ValueError("requested_effort must be a structured effort level")
        if self.effective_effort is not None and self.effective_effort not in legal_efforts:
            raise ValueError("effective_effort must be a structured effort level")


@dataclass(frozen=True)
class UserFeedbackPayload:
    user_action_ref: EvidenceRef
    free_text_hash: str | None = None
    classifier_note: str | None = None

    def __post_init__(self) -> None:
        if self.free_text_hash is not None:
            _validate_sha256(self.free_text_hash, "free_text_hash")
        if self.classifier_note is not None:
            _validate_code(self.classifier_note, "classifier_note")


@dataclass(frozen=True)
class ModelReportPayload:
    native_message_ref: EvidenceRef
    text_hash: str
    raw_kind: str | None = None

    def __post_init__(self) -> None:
        _validate_sha256(self.text_hash, "text_hash")
        if self.raw_kind is not None:
            _validate_code(self.raw_kind, "raw_kind")


SignalPayload = (
    ValidatorOutcomePayload
    | ExecutionOutcomePayload
    | PendingLostPayload
    | TurnOutcomePayload
    | UserFeedbackPayload
    | ModelReportPayload
)


_FAMILY_PAYLOAD_TYPES: dict[SignalFamily, tuple[type[Any], ...]] = {
    SignalFamily.VALIDATOR_OUTCOME: (ValidatorOutcomePayload,),
    SignalFamily.EXECUTION_OBSERVATION: (
        ExecutionOutcomePayload,
        PendingLostPayload,
    ),
    SignalFamily.TURN_OUTCOME: (TurnOutcomePayload,),
    SignalFamily.USER_FEEDBACK: (UserFeedbackPayload,),
    SignalFamily.MODEL_REPORT: (ModelReportPayload,),
}


class FailureMode(str, Enum):
    VALIDATION_FAILED = "validation_failed"
    EXECUTION_FAILED = "execution_failed"
    PENDING_LOST = "pending_lost"
    USER_REJECTED = "user_rejected"
    UNKNOWN = "unknown"


class CausalHypothesis(str, Enum):
    ENVIRONMENT = "environment"
    MIXED = "mixed"
    UNKNOWN = "unknown"


class RequiredAction(str, Enum):
    RETRY = "retry"
    VERIFY = "verify"
    ASK_USER_AGAIN = "ask_user_again"
    RECONCILE = "reconcile"
    NONE = "none"


@dataclass(frozen=True)
class OutcomeAttribution:
    attempt_id: str
    observed_outcome: str
    failure_mode: FailureMode
    causal_hypothesis: CausalHypothesis
    required_action: RequiredAction
    supporting_signal_ids: tuple[str, ...] = ()
    excluded_signal_ids: tuple[str, ...] = ()
    attribution_policy_version: str = ""


@dataclass(frozen=True)
class DerivedContradiction:
    contradiction_id: str
    left_signal_id: str
    right_signal_id: str
    relation: str
    strength: Reliability
    detector_version: str

    def __post_init__(self) -> None:
        if self.left_signal_id == self.right_signal_id:
            raise ValueError("contradiction requires two distinct signals")


@dataclass(frozen=True)
class RouteEvidenceBundle:
    eligible_trigger_ids: tuple[str, ...]
    advisory_ids: tuple[str, ...]
    excluded_ids: tuple[str, ...]
    supporting_signal_refs: tuple[str, ...]
    actual_outcome: OutcomeAttribution | None
    negative_sample_marker: bool
    policy_version: str


@dataclass(frozen=True)
class CognitiveSignalDraft:
    """尚未由权威入口写入 provenance 和落库时间的信号草稿。"""

    native_event_id: str
    observation_ordinal: int
    kind: SignalKind
    reliability: Reliability
    validity: SignalValidity
    subject: SignalSubject
    attempt_id: str
    comparison_key: AttemptComparisonKey
    payload: SignalPayload
    evidence_refs: tuple[EvidenceRef, ...]
    observed_at: str
    normalizer_version: str
    comparison_key_version: str
    causal_parent_ref: CausalLink | None = None

    def __post_init__(self) -> None:
        if not self.native_event_id or self.observation_ordinal < 0:
            raise ValueError("source event identity is required")
        _validate_signal_shape(self)


@dataclass(frozen=True)
class CognitiveSignal:
    signal_id: str
    native_event_id: str
    observation_ordinal: int
    kind: SignalKind
    provenance: Provenance
    reliability: Reliability
    validity: SignalValidity
    subject: SignalSubject
    attempt_id: str
    comparison_key: AttemptComparisonKey
    payload: SignalPayload
    evidence_refs: tuple[EvidenceRef, ...]
    observed_at: str
    recorded_at: str
    normalizer_version: str
    comparison_key_version: str
    policy_version: str
    causal_parent_ref: CausalLink | None = None

    def __post_init__(self) -> None:
        _validate_signal_shape(self)
        expected = signal_id_for(
            runtime=self.comparison_key.runtime,
            native_event_id=self.native_event_id,
            kind=self.kind,
            observation_ordinal=self.observation_ordinal,
            normalizer_version=self.normalizer_version,
        )
        if self.signal_id != expected:
            raise ValueError("signal_id does not match source identity")
        if not self.recorded_at or not self.policy_version:
            raise ValueError("recorded_at and policy_version are required")
        _validate_utc_iso(self.recorded_at, "recorded_at")
        if self.provenance is Provenance.MODEL_HYPOTHESIS and (
            self.kind.family is not SignalFamily.MODEL_REPORT
            or self.reliability is not Reliability.WEAK
        ):
            raise ValueError("model provenance is limited to weak model reports")
        if self.provenance is Provenance.USER_DECISION and (
            self.kind.family is not SignalFamily.USER_FEEDBACK
        ):
            raise ValueError("user provenance is limited to user feedback")
        if self.provenance in (Provenance.STALE, Provenance.UNKNOWN):
            raise ValueError("stale or unknown authority cannot persist a signal")


@dataclass(frozen=True)
class UnknownCognitiveSignal:
    """保留未来未知 family/subtype 原始字段的 projection 行。"""

    signal_id: str
    family: str
    subtype: str
    task_id: str | None
    episode_id: str | None
    attempt_id: str
    observed_at: str
    recorded_at: str
    raw_payload: Mapping[str, Any]


StoredCognitiveSignal = CognitiveSignal | UnknownCognitiveSignal


@dataclass(frozen=True)
class AttemptBinding:
    attempt_id: str
    runtime: str
    task_id: str | None
    episode_id: str | None
    native_session_id: str
    binding_generation: str

    def __post_init__(self) -> None:
        _validate_structural_label(self.attempt_id, "AttemptBinding.attempt_id")
        _validate_structural_label(self.runtime, "AttemptBinding.runtime")
        if self.task_id is not None:
            _validate_structural_label(self.task_id, "AttemptBinding.task_id")
        if self.episode_id is not None:
            _validate_structural_label(self.episode_id, "AttemptBinding.episode_id")


class SignalAuthorityRegistry(Protocol):
    def adapter_runtime(self, adapter_identity: str) -> str | None: ...

    def attempt_binding(self, attempt_id: str) -> AttemptBinding | None: ...

    def reference_metadata(
        self, reference: EvidenceRef
    ) -> RegisteredEvidence | None: ...

    def validator_invocation(
        self, native_tool_item_id: str
    ) -> ValidatorInvocationEvidence | None: ...


class InMemorySignalAuthorityRegistry:
    """供隔离 Store 与测试显式注入的内存 authority。"""

    def __init__(self) -> None:
        self._adapters: dict[str, str] = {}
        self._attempts: dict[str, AttemptBinding] = {}
        self._references: dict[
            tuple[str, str, str | None, str | None], RegisteredEvidence
        ] = {}
        self._validator_invocations: dict[str, ValidatorInvocationEvidence] = {}

    def trust_adapter(self, adapter_identity: str, runtime: str) -> None:
        self._adapters[adapter_identity] = runtime

    def register_attempt(self, binding: AttemptBinding) -> None:
        self._attempts[binding.attempt_id] = binding

    def register_reference(
        self,
        reference: EvidenceRef,
        *,
        attempt_id: str | None = None,
        authority: EvidenceAuthority = EvidenceAuthority.RUNTIME_OBSERVATION,
        action_subtype: str | None = None,
    ) -> None:
        resolved_attempt = attempt_id or reference.invocation_id
        binding = self._attempts.get(resolved_attempt or "")
        if binding is None:
            raise ValueError("reference registration requires a known attempt")
        self._references[_reference_identity(reference)] = RegisteredEvidence(
            reference=reference,
            authority=authority,
            attempt_id=binding.attempt_id,
            runtime=binding.runtime,
            task_id=binding.task_id,
            episode_id=binding.episode_id,
            action_subtype=action_subtype,
        )

    def register_validator_invocation(
        self, invocation: ValidatorInvocationEvidence
    ) -> None:
        if invocation.attempt_id not in self._attempts:
            raise ValueError("validator invocation requires a known attempt")
        self._validator_invocations[invocation.native_tool_item_id] = invocation

    def adapter_runtime(self, adapter_identity: str) -> str | None:
        return self._adapters.get(adapter_identity)

    def attempt_binding(self, attempt_id: str) -> AttemptBinding | None:
        return self._attempts.get(attempt_id)

    def reference_metadata(self, reference: EvidenceRef) -> RegisteredEvidence | None:
        return self._references.get(_reference_identity(reference))

    def validator_invocation(
        self, native_tool_item_id: str
    ) -> ValidatorInvocationEvidence | None:
        return self._validator_invocations.get(native_tool_item_id)


@dataclass(frozen=True)
class RecordedSignal:
    signal: CognitiveSignal
    event_seq: int
    inserted: bool


@dataclass(frozen=True)
class SignalPage:
    items: tuple[StoredCognitiveSignal, ...]
    next_cursor: str | None


@dataclass(frozen=True)
class SignalNormalizationContext:
    attempt_id: str
    subject: SignalSubject
    comparison_key: AttemptComparisonKey
    evidence_refs: tuple[EvidenceRef, ...]
    native_event_id: str
    observation_ordinal: int
    observed_at: str
    normalizer_version: str = "normalizer-v1"
    comparison_key_version: str = "comparison-v1"
    pending_disposition: tuple[str, str, str] | None = None
    binding_generation: str | None = None


class SignalCommandError(ValueError):
    pass


class LateSignalRejected(ValueError):
    def __init__(
        self,
        signal_id: str,
        reason: str,
        *,
        attempt_id: str | None = None,
        task_id: str | None = None,
        episode_id: str | None = None,
    ) -> None:
        self.signal_id = signal_id
        self.reason = reason
        self.attempt_id = attempt_id
        self.task_id = task_id
        self.episode_id = episode_id
        super().__init__(f"signal {signal_id!r} rejected: {reason}")


def _reference_identity(ref: EvidenceRef) -> tuple[str, str, str | None, str | None]:
    return (ref.namespace, ref.ref_id, ref.runtime, ref.invocation_id)


def _validate_signal_shape(value: CognitiveSignalDraft | CognitiveSignal) -> None:
    allowed = _FAMILY_PAYLOAD_TYPES.get(value.kind.family, ())
    if not isinstance(value.payload, allowed):
        raise ValueError(
            f"payload type {type(value.payload).__name__} does not match "
            f"{value.kind.family.value}"
        )
    if isinstance(value.payload, ValidatorOutcomePayload):
        expected_subtype = "pass" if value.payload.exit_code == 0 else "fail"
        if value.kind.subtype != expected_subtype:
            raise ValueError("validator subtype does not match exit_code")
    is_pending = value.kind.subtype == "pending_lost"
    has_pending_payload = isinstance(value.payload, PendingLostPayload)
    if is_pending != has_pending_payload:
        expected = "PendingLostPayload" if is_pending else "pending_lost subtype"
        raise ValueError(f"pending_lost and PendingLostPayload must pair: {expected}")
    if isinstance(value.payload, ExecutionOutcomePayload):
        retry_values = (
            value.payload.retry_attempt,
            value.payload.retry_max,
            value.payload.retry_delay_ms,
        )
        if value.kind.subtype != "retry" and any(
            item is not None for item in retry_values
        ):
            raise ValueError("retry metadata is limited to retry observations")
        numeric_values = tuple(item for item in retry_values if item is not None)
        if any(item < 0 for item in numeric_values):
            raise ValueError("retry metadata must be non-negative")
        if (
            value.payload.retry_attempt is not None
            and value.payload.retry_max is not None
            and value.payload.retry_attempt > value.payload.retry_max
        ):
            raise ValueError("retry_attempt cannot exceed retry_max")
    if not isinstance(value.validity, (PointFact, StateSample, PendingState)):
        raise ValueError("invalid signal validity")
    if not isinstance(value.subject, (AttemptRef, EpisodeRef, TaskRef)):
        raise ValueError("global signal subject is forbidden")
    if not value.evidence_refs:
        raise ValueError("a signal requires evidence_refs")
    if not value.attempt_id or not value.observed_at:
        raise ValueError("attempt_id and observed_at are required")
    _validate_utc_iso(value.observed_at, "observed_at")
    if not value.normalizer_version or not value.comparison_key_version:
        raise ValueError("normalizer and comparison key versions are required")
    _validate_structural_label(value.normalizer_version, "normalizer_version")
    _validate_structural_label(value.comparison_key_version, "comparison_key_version")


def signal_id_for(
    *,
    runtime: str,
    native_event_id: str,
    kind: SignalKind,
    observation_ordinal: int,
    normalizer_version: str,
) -> str:
    canonical = "\x1f".join(
        (
            runtime,
            _stable_source_id(native_event_id),
            kind.family.value,
            kind.subtype,
            str(observation_ordinal),
            normalizer_version,
        )
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


def hash_text(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode()).hexdigest()


def _stable_source_id(value: str) -> str:
    if re.fullmatch(r"sha256:[0-9a-f]{64}", value):
        return value
    return hash_text(value)


def build_signal(
    draft: CognitiveSignalDraft,
    *,
    provenance: Provenance,
    recorded_at: str,
    policy_version: str,
) -> CognitiveSignal:
    return CognitiveSignal(
        signal_id=signal_id_for(
            runtime=draft.comparison_key.runtime,
            native_event_id=draft.native_event_id,
            kind=draft.kind,
            observation_ordinal=draft.observation_ordinal,
            normalizer_version=draft.normalizer_version,
        ),
        native_event_id=_stable_source_id(draft.native_event_id),
        observation_ordinal=draft.observation_ordinal,
        kind=draft.kind,
        provenance=provenance,
        reliability=draft.reliability,
        validity=draft.validity,
        subject=draft.subject,
        attempt_id=draft.attempt_id,
        comparison_key=draft.comparison_key,
        payload=draft.payload,
        evidence_refs=draft.evidence_refs,
        observed_at=draft.observed_at,
        recorded_at=recorded_at,
        normalizer_version=draft.normalizer_version,
        comparison_key_version=draft.comparison_key_version,
        policy_version=policy_version,
        causal_parent_ref=draft.causal_parent_ref,
    )


def signal_payload_evidence_refs(payload: SignalPayload) -> tuple[EvidenceRef, ...]:
    if isinstance(payload, ValidatorOutcomePayload):
        return tuple(
            ref for ref in (payload.exit_event_ref, payload.output_ref) if ref is not None
        )
    if isinstance(payload, ExecutionOutcomePayload):
        return (payload.native_item_ref,) if payload.native_item_ref else ()
    if isinstance(payload, PendingLostPayload):
        return (payload.terminal_event_ref,) if payload.terminal_event_ref else ()
    if isinstance(payload, TurnOutcomePayload):
        return (payload.terminal_event_ref,)
    if isinstance(payload, UserFeedbackPayload):
        return (payload.user_action_ref,)
    return (payload.native_message_ref,)


def draft_with_persisted_evidence(
    draft: CognitiveSignalDraft,
) -> CognitiveSignalDraft:
    """把 native 引用身份转为稳定哈希，避免引用字段承载正文。"""

    def safe_ref(ref: EvidenceRef | None) -> EvidenceRef | None:
        if ref is None:
            return None
        return replace(
            ref,
            ref_id=_stable_source_id(ref.ref_id),
            invocation_id=(
                _stable_source_id(ref.invocation_id)
                if ref.invocation_id is not None
                else None
            ),
        )

    payload = draft.payload
    if isinstance(payload, ValidatorOutcomePayload):
        exit_ref = safe_ref(payload.exit_event_ref)
        assert exit_ref is not None
        safe_payload: SignalPayload = replace(
            payload,
            native_tool_item_id=_stable_source_id(payload.native_tool_item_id),
            exit_event_ref=exit_ref,
            output_ref=safe_ref(payload.output_ref),
        )
    elif isinstance(payload, ExecutionOutcomePayload):
        safe_payload = replace(payload, native_item_ref=safe_ref(payload.native_item_ref))
    elif isinstance(payload, PendingLostPayload):
        safe_payload = replace(
            payload,
            native_binding_generation=(
                _stable_source_id(payload.native_binding_generation)
                if payload.native_binding_generation is not None
                else None
            ),
            terminal_event_ref=safe_ref(payload.terminal_event_ref),
        )
    elif isinstance(payload, TurnOutcomePayload):
        terminal_ref = safe_ref(payload.terminal_event_ref)
        assert terminal_ref is not None
        safe_payload = replace(payload, terminal_event_ref=terminal_ref)
    elif isinstance(payload, UserFeedbackPayload):
        action_ref = safe_ref(payload.user_action_ref)
        assert action_ref is not None
        safe_payload = replace(payload, user_action_ref=action_ref)
    else:
        message_ref = safe_ref(payload.native_message_ref)
        assert message_ref is not None
        safe_payload = replace(payload, native_message_ref=message_ref)
    return replace(
        draft,
        comparison_key=replace(
            draft.comparison_key,
            target_ref=(
                _stable_source_id(draft.comparison_key.target_ref)
                if draft.comparison_key.target_ref is not None
                else None
            ),
        ),
        payload=safe_payload,
        evidence_refs=tuple(
            ref for item in draft.evidence_refs if (ref := safe_ref(item)) is not None
        ),
    )


def _evidence_to_dict(ref: EvidenceRef | None) -> dict[str, Any] | None:
    if ref is None:
        return None
    return {
        "namespace": ref.namespace,
        "ref_id": ref.ref_id,
        "runtime": ref.runtime,
        "event_kind": ref.event_kind,
        "content_hash": ref.content_hash,
        "invocation_id": ref.invocation_id,
    }


def _evidence_from_dict(value: Mapping[str, Any] | None) -> EvidenceRef | None:
    if value is None:
        return None
    return EvidenceRef(
        namespace=str(value["namespace"]),
        ref_id=str(value["ref_id"]),
        runtime=_optional_str(value.get("runtime")),
        event_kind=_optional_str(value.get("event_kind")),
        content_hash=_optional_str(value.get("content_hash")),
        invocation_id=_optional_str(value.get("invocation_id")),
    )


def _payload_to_dict(payload: SignalPayload) -> dict[str, Any]:
    if isinstance(payload, ValidatorOutcomePayload):
        return {
            "type": "validator_outcome",
            "intent_id": payload.intent_id,
            "native_tool_item_id": payload.native_tool_item_id,
            "normalized_argv": list(payload.normalized_argv),
            "exit_event_ref": _evidence_to_dict(payload.exit_event_ref),
            "output_ref": _evidence_to_dict(payload.output_ref),
            "exit_code": payload.exit_code,
            "completed": payload.completed,
        }
    if isinstance(payload, ExecutionOutcomePayload):
        return {
            "type": "execution_outcome",
            "category": payload.category.value,
            "native_item_ref": _evidence_to_dict(payload.native_item_ref),
            "retry_attempt": payload.retry_attempt,
            "retry_max": payload.retry_max,
            "retry_delay_ms": payload.retry_delay_ms,
        }
    if isinstance(payload, PendingLostPayload):
        return {
            "type": "pending_lost",
            "resolution_state": payload.resolution_state,
            "effect_certainty": payload.effect_certainty,
            "required_action": payload.required_action,
            "native_binding_generation": payload.native_binding_generation,
            "terminal_event_ref": _evidence_to_dict(payload.terminal_event_ref),
        }
    if isinstance(payload, TurnOutcomePayload):
        return {
            "type": "turn_outcome",
            "terminal_event_ref": _evidence_to_dict(payload.terminal_event_ref),
            "requested_effort": payload.requested_effort,
            "effective_effort": payload.effective_effort,
        }
    if isinstance(payload, UserFeedbackPayload):
        return {
            "type": "user_feedback",
            "user_action_ref": _evidence_to_dict(payload.user_action_ref),
            "free_text_hash": payload.free_text_hash,
            "classifier_note": payload.classifier_note,
        }
    return {
        "type": "model_report",
        "native_message_ref": _evidence_to_dict(payload.native_message_ref),
        "text_hash": payload.text_hash,
        "raw_kind": payload.raw_kind,
    }


def _payload_from_dict(value: Mapping[str, Any]) -> SignalPayload:
    payload_type = value.get("type")
    if payload_type == "validator_outcome":
        exit_ref = _evidence_from_dict(_mapping(value.get("exit_event_ref")))
        assert exit_ref is not None
        return ValidatorOutcomePayload(
            intent_id=str(value["intent_id"]),
            native_tool_item_id=str(value["native_tool_item_id"]),
            normalized_argv=tuple(str(item) for item in value["normalized_argv"]),
            exit_event_ref=exit_ref,
            output_ref=_evidence_from_dict(_mapping(value.get("output_ref"))),
            exit_code=int(value["exit_code"]),
            completed=bool(value["completed"]),
        )
    if payload_type == "execution_outcome":
        return ExecutionOutcomePayload(
            category=ToolFailureCategory(value["category"]),
            native_item_ref=_evidence_from_dict(_mapping(value.get("native_item_ref"))),
            retry_attempt=_optional_int(value.get("retry_attempt")),
            retry_max=_optional_int(value.get("retry_max")),
            retry_delay_ms=_optional_float(value.get("retry_delay_ms")),
        )
    if payload_type == "pending_lost":
        return PendingLostPayload(
            resolution_state=str(value["resolution_state"]),
            effect_certainty=str(value["effect_certainty"]),
            required_action=str(value["required_action"]),
            native_binding_generation=_optional_str(
                value.get("native_binding_generation")
            ),
            terminal_event_ref=_evidence_from_dict(
                _mapping(value.get("terminal_event_ref"))
            ),
        )
    if payload_type == "turn_outcome":
        terminal = _evidence_from_dict(_mapping(value.get("terminal_event_ref")))
        assert terminal is not None
        return TurnOutcomePayload(
            terminal_event_ref=terminal,
            requested_effort=_optional_str(value.get("requested_effort")),
            effective_effort=_optional_str(value.get("effective_effort")),
        )
    if payload_type == "user_feedback":
        action = _evidence_from_dict(_mapping(value.get("user_action_ref")))
        assert action is not None
        return UserFeedbackPayload(
            user_action_ref=action,
            free_text_hash=_optional_str(value.get("free_text_hash")),
            classifier_note=_optional_str(value.get("classifier_note")),
        )
    if payload_type == "model_report":
        message = _evidence_from_dict(_mapping(value.get("native_message_ref")))
        assert message is not None
        return ModelReportPayload(
            native_message_ref=message,
            text_hash=str(value["text_hash"]),
            raw_kind=_optional_str(value.get("raw_kind")),
        )
    raise ValueError(f"unknown signal payload type: {payload_type!r}")


def signal_to_dict(signal: CognitiveSignal) -> dict[str, Any]:
    if isinstance(signal.validity, PointFact):
        validity: dict[str, Any] = {"kind": "point_fact"}
    elif isinstance(signal.validity, StateSample):
        validity = {"kind": "state_sample", "valid_until": signal.validity.valid_until}
    else:
        validity = {
            "kind": "pending_state",
            "terminal_event_ref": signal.validity.terminal_event_ref,
        }
    if isinstance(signal.subject, AttemptRef):
        subject = {"kind": "attempt", "attempt_id": signal.subject.attempt_id}
    elif isinstance(signal.subject, EpisodeRef):
        subject = {"kind": "episode", "episode_id": signal.subject.episode_id}
    else:
        subject = {"kind": "task", "task_id": signal.subject.task_id}
    return {
        "signal_id": signal.signal_id,
        "native_event_id": signal.native_event_id,
        "observation_ordinal": signal.observation_ordinal,
        "kind": {"family": signal.kind.family.value, "subtype": signal.kind.subtype},
        "provenance": signal.provenance.value,
        "reliability": signal.reliability.value,
        "validity": validity,
        "subject": subject,
        "attempt_id": signal.attempt_id,
        "comparison_key": {
            "runtime": signal.comparison_key.runtime,
            "attempt_category": signal.comparison_key.attempt_category,
            "task_id": signal.comparison_key.task_id,
            "target_ref": signal.comparison_key.target_ref,
            "validator_intent_id": signal.comparison_key.validator_intent_id,
        },
        "payload": _payload_to_dict(signal.payload),
        "evidence_refs": [_evidence_to_dict(ref) for ref in signal.evidence_refs],
        "observed_at": signal.observed_at,
        "recorded_at": signal.recorded_at,
        "normalizer_version": signal.normalizer_version,
        "comparison_key_version": signal.comparison_key_version,
        "policy_version": signal.policy_version,
        "causal_parent_ref": (
            {
                "relation": signal.causal_parent_ref.relation.value,
                "parent_signal_id": signal.causal_parent_ref.parent_signal_id,
            }
            if signal.causal_parent_ref
            else None
        ),
    }


def signal_from_dict(value: Mapping[str, Any]) -> CognitiveSignal:
    kind_value = _required_mapping(value, "kind")
    validity_value = _required_mapping(value, "validity")
    validity_kind = validity_value["kind"]
    if validity_kind == "point_fact":
        validity: SignalValidity = PointFact()
    elif validity_kind == "state_sample":
        validity = StateSample(str(validity_value["valid_until"]))
    elif validity_kind == "pending_state":
        validity = PendingState(_optional_str(validity_value.get("terminal_event_ref")))
    else:
        raise ValueError(f"unknown validity kind: {validity_kind!r}")
    subject_value = _required_mapping(value, "subject")
    subject_kind = subject_value["kind"]
    if subject_kind == "attempt":
        subject: SignalSubject = AttemptRef(str(subject_value["attempt_id"]))
    elif subject_kind == "episode":
        subject = EpisodeRef(str(subject_value["episode_id"]))
    elif subject_kind == "task":
        subject = TaskRef(str(subject_value["task_id"]))
    else:
        raise ValueError(f"unknown subject kind: {subject_kind!r}")
    comparison = _required_mapping(value, "comparison_key")
    causal_value = _mapping(value.get("causal_parent_ref"))
    return CognitiveSignal(
        signal_id=str(value["signal_id"]),
        native_event_id=str(value["native_event_id"]),
        observation_ordinal=int(value["observation_ordinal"]),
        kind=SignalKind(
            SignalFamily(kind_value["family"]), str(kind_value["subtype"])
        ),
        provenance=Provenance(value["provenance"]),
        reliability=Reliability(value["reliability"]),
        validity=validity,
        subject=subject,
        attempt_id=str(value["attempt_id"]),
        comparison_key=AttemptComparisonKey(
            runtime=str(comparison["runtime"]),
            attempt_category=str(comparison["attempt_category"]),
            task_id=_optional_str(comparison.get("task_id")),
            target_ref=_optional_str(comparison.get("target_ref")),
            validator_intent_id=_optional_str(comparison.get("validator_intent_id")),
        ),
        payload=_payload_from_dict(_required_mapping(value, "payload")),
        evidence_refs=tuple(
            ref
            for item in value["evidence_refs"]
            if (ref := _evidence_from_dict(_mapping(item))) is not None
        ),
        observed_at=str(value["observed_at"]),
        recorded_at=str(value["recorded_at"]),
        normalizer_version=str(value["normalizer_version"]),
        comparison_key_version=str(value["comparison_key_version"]),
        policy_version=str(value["policy_version"]),
        causal_parent_ref=(
            CausalLink(
                CausalRelation(causal_value["relation"]),
                str(causal_value["parent_signal_id"]),
            )
            if causal_value
            else None
        ),
    )


def unknown_signal_from_dict(value: Mapping[str, Any]) -> UnknownCognitiveSignal:
    kind = _mapping(value.get("kind")) or {}
    comparison = _mapping(value.get("comparison_key")) or {}
    subject = _mapping(value.get("subject")) or {}
    task_id = _optional_str(comparison.get("task_id"))
    episode_id = _optional_str(subject.get("episode_id"))
    return UnknownCognitiveSignal(
        signal_id=str(value.get("signal_id", "unknown")),
        family=str(kind.get("family", "unknown")),
        subtype=str(kind.get("subtype", "unknown")),
        task_id=task_id,
        episode_id=episode_id,
        attempt_id=str(value.get("attempt_id", "unknown")),
        observed_at=str(value.get("observed_at", "")),
        recorded_at=str(value.get("recorded_at", "")),
        raw_payload=dict(value),
    )


def _mapping(value: Any) -> Mapping[str, Any] | None:
    return value if isinstance(value, Mapping) else None


def _required_mapping(value: Mapping[str, Any], key: str) -> Mapping[str, Any]:
    mapped = _mapping(value.get(key))
    if mapped is None:
        raise ValueError(f"{key} must be an object")
    return mapped


def _optional_str(value: Any) -> str | None:
    return None if value is None else str(value)


def _optional_int(value: Any) -> int | None:
    return None if value is None else int(value)


def _optional_float(value: Any) -> float | None:
    return None if value is None else float(value)


def _validate_utc_iso(value: str, field: str) -> None:
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{field} must be ISO-8601") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        raise ValueError(f"{field} must be UTC")


def _validate_sha256(value: str, field: str) -> None:
    if re.fullmatch(r"sha256:[0-9a-f]{64}", value) is None:
        raise ValueError(f"{field} must be a sha256 digest")


def _validate_code(value: str, field: str) -> None:
    if re.fullmatch(r"[a-z0-9_.:-]{1,64}", value) is None:
        raise ValueError(f"{field} must be a short structured code")


def _validate_structural_label(value: str, field: str) -> None:
    if re.fullmatch(r"[A-Za-z0-9_.:/-]{1,128}", value) is None:
        raise ValueError(f"{field} must be a short structural label")
