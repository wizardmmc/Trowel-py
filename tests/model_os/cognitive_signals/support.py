from __future__ import annotations

from trowel_py.model_os.cognitive_signals import (
    AttemptBinding,
    AttemptComparisonKey,
    AttemptRef,
    CognitiveSignalDraft,
    EvidenceRef,
    InMemorySignalAuthorityRegistry,
    PointFact,
    Reliability,
    SignalFamily,
    SignalKind,
    ToolFailureCategory,
    ExecutionOutcomePayload,
)


def authority_for(
    *,
    attempt_id: str = "attempt-1",
    runtime: str = "cc",
    task_id: str | None = "task-1",
    episode_id: str | None = "episode-1",
    native_session_id: str = "session-1",
    binding_generation: str = "generation-1",
) -> InMemorySignalAuthorityRegistry:
    authority = InMemorySignalAuthorityRegistry()
    authority.trust_adapter("trusted-adapter", runtime)
    authority.register_attempt(
        AttemptBinding(
            attempt_id=attempt_id,
            runtime=runtime,
            task_id=task_id,
            episode_id=episode_id,
            native_session_id=native_session_id,
            binding_generation=binding_generation,
        )
    )
    return authority


def execution_draft(
    *,
    attempt_id: str = "attempt-1",
    runtime: str = "cc",
    task_id: str | None = "task-1",
    native_event_id: str = "native-event-1",
    category: ToolFailureCategory = ToolFailureCategory.NETWORK,
    evidence_ref: EvidenceRef | None = None,
) -> CognitiveSignalDraft:
    evidence = evidence_ref or EvidenceRef(
        namespace=runtime,
        ref_id=native_event_id,
        runtime=runtime,
        invocation_id=attempt_id,
    )
    return CognitiveSignalDraft(
        native_event_id=native_event_id,
        observation_ordinal=0,
        kind=SignalKind(SignalFamily.EXECUTION_OBSERVATION, "tool_error"),
        reliability=Reliability.RELIABLE,
        validity=PointFact(),
        subject=AttemptRef(attempt_id),
        attempt_id=attempt_id,
        comparison_key=AttemptComparisonKey(
            runtime=runtime,
            attempt_category="tool:bash",
            task_id=task_id,
            target_ref="tests/example.py",
        ),
        payload=ExecutionOutcomePayload(category=category, native_item_ref=evidence),
        evidence_refs=(evidence,),
        observed_at="2026-07-25T01:00:00+00:00",
        normalizer_version="normalizer-v1",
        comparison_key_version="comparison-v1",
    )
