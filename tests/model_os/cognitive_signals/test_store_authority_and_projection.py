from __future__ import annotations

import json
import sqlite3
from dataclasses import replace

import pytest

from trowel_py.model_os.cognitive_signals import (
    AttemptBinding,
    CausalLink,
    CausalRelation,
    EpisodeRef,
    EvidenceAuthority,
    EvidenceRef,
    InMemorySignalAuthorityRegistry,
    LateSignalRejected,
    ModelReportPayload,
    PendingLostPayload,
    PendingState,
    Reliability,
    SignalCommandError,
    SignalFamily,
    SignalKind,
    TurnOutcomePayload,
    UserFeedbackPayload,
    UnknownCognitiveSignal,
    ValidatorInvocationEvidence,
    ValidatorOutcomePayload,
    hash_text,
)
from trowel_py.model_os.redaction import redact_payload
from trowel_py.model_os.signal_projection import insert_projection_row
from trowel_py.model_os.store import ModelOsStore
from trowel_py.model_os.types import EventEnvelope, EventKind, Provenance
from trowel_py.model_os.types import PendingDescriptor, ReconcileReason, WaitingSubtype

from tests.model_os._episode_helpers import (
    FakeClock,
    activate_episode,
    make_running_task_episode,
)

from .support import authority_for, execution_draft


def _opened_store(db_path, authority) -> ModelOsStore:
    store = ModelOsStore(db_path, signal_authority=authority)
    store.open()
    return store


def test_runtime_signal_is_idempotent_by_source_not_payload(db_path) -> None:
    authority = authority_for()
    for ref_id in ("native-event-1", "native-event-2"):
        authority.register_reference(
            EvidenceRef("cc", ref_id, runtime="cc", invocation_id="attempt-1")
        )
    store = _opened_store(db_path, authority)
    try:
        first = store.record_runtime_observation(
            execution_draft(),
            adapter_identity="trusted-adapter",
            binding_generation="generation-1",
        )
        replay = store.record_runtime_observation(
            execution_draft(),
            adapter_identity="trusted-adapter",
            binding_generation="generation-1",
        )
        independent = store.record_runtime_observation(
            execution_draft(native_event_id="native-event-2"),
            adapter_identity="trusted-adapter",
            binding_generation="generation-1",
        )
        assert first.event_seq == replay.event_seq
        assert first.inserted is True
        assert replay.inserted is False
        assert independent.signal.signal_id != first.signal.signal_id
        page = store.signals_for_task(
            "task-1", as_of="2026-07-25T02:00:00+00:00", limit=10
        )
        assert len(page.items) == 2
    finally:
        store.close()


@pytest.mark.parametrize(
    "adapter,generation",
    [("attacker", "generation-1"), ("trusted-adapter", "old-generation")],
)
def test_bad_runtime_authority_is_rejected_and_audited(
    db_path, adapter: str, generation: str
) -> None:
    authority = authority_for()
    authority.register_reference(
        EvidenceRef("cc", "native-event-1", runtime="cc", invocation_id="attempt-1")
    )
    store = _opened_store(db_path, authority)
    try:
        store.record_runtime_observation(
            execution_draft(),
            adapter_identity="trusted-adapter",
            binding_generation="generation-1",
        )
        with pytest.raises(LateSignalRejected):
            store.record_runtime_observation(
                execution_draft(),
                adapter_identity=adapter,
                binding_generation=generation,
            )
        events = [event for _, event in store.list_events()]
        assert [event.kind for event in events] == [
            EventKind.COGNITIVE_SIGNAL_RECORDED,
            EventKind.LATE_SIGNAL_REJECTED,
        ]
        assert events[-1].task_id == "task-1"
        assert events[-1].payload["attempt_id"] == hash_text("attempt-1")
    finally:
        store.close()


def test_bare_append_cannot_forge_machine_signal(store: ModelOsStore) -> None:
    forged = EventEnvelope(
        event_id="forged-signal",
        kind=EventKind.COGNITIVE_SIGNAL_RECORDED,
        occurred_at="2026-07-25T01:00:00+00:00",
        source="attacker",
        provenance=Provenance.MACHINE_OBSERVATION,
        policy_version="v1",
        payload={"signal_id": "forged"},
    )
    with pytest.raises(SignalCommandError):
        store.append_event(forged)


def test_validator_signal_requires_trusted_invocation_exit_and_target(db_path) -> None:
    authority = authority_for()
    exit_ref = EvidenceRef(
        "validator_exit", "exit-1", runtime="cc", invocation_id="tool-item-1"
    )
    output_ref = EvidenceRef(
        "validator_output", "output-1", runtime="cc", invocation_id="tool-item-1"
    )
    authority.register_reference(
        exit_ref,
        attempt_id="attempt-1",
        authority=EvidenceAuthority.VALIDATOR_EXIT,
    )
    authority.register_reference(
        output_ref,
        attempt_id="attempt-1",
        authority=EvidenceAuthority.VALIDATOR_OUTPUT,
    )
    argv = (".venv/bin/python", "-m", "pytest", "-q", "tests/model_os")
    invocation = ValidatorInvocationEvidence(
        native_tool_item_id="tool-item-1",
        intent_id="pytest",
        attempt_id="attempt-1",
        runtime="cc",
        normalized_argv=argv,
        exit_event_ref=exit_ref,
        output_ref=output_ref,
        exit_code=1,
        completed=True,
    )
    authority.register_validator_invocation(invocation)
    base = execution_draft(evidence_ref=exit_ref)
    draft = replace(
        base,
        kind=SignalKind(SignalFamily.VALIDATOR_OUTCOME, "fail"),
        comparison_key=replace(
            base.comparison_key,
            attempt_category="validator:pytest",
            target_ref="tests/model_os",
            validator_intent_id="pytest",
        ),
        payload=ValidatorOutcomePayload(
            "pytest", "tool-item-1", argv, exit_ref, output_ref, 1, True
        ),
        evidence_refs=(exit_ref, output_ref),
    )
    store = _opened_store(db_path, authority)
    try:
        recorded = store.record_runtime_observation(
            draft,
            adapter_identity="trusted-adapter",
            binding_generation="generation-1",
        )
        assert recorded.signal.kind.subtype == "fail"
        assert recorded.signal.comparison_key.target_ref == hash_text("tests/model_os")
        assert isinstance(recorded.signal.payload, ValidatorOutcomePayload)
        assert recorded.signal.payload.normalized_argv[0] == "validator:pytest"

        with pytest.raises(LateSignalRejected, match="comparison_key"):
            store.record_runtime_observation(
                replace(
                    draft,
                    native_event_id="forged-target",
                    comparison_key=replace(
                        draft.comparison_key, target_ref="tests/another_target"
                    ),
                ),
                adapter_identity="trusted-adapter",
                binding_generation="generation-1",
            )
        with pytest.raises(LateSignalRejected, match="invocation"):
            store.record_runtime_observation(
                replace(
                    draft,
                    native_event_id="forged-exit",
                    kind=SignalKind(SignalFamily.VALIDATOR_OUTCOME, "pass"),
                    payload=replace(draft.payload, exit_code=0),
                ),
                adapter_identity="trusted-adapter",
                binding_generation="generation-1",
            )
        invalid_argv = ("pytest", "-q=not-valid", "tests/model_os")
        authority.register_validator_invocation(
            replace(invocation, normalized_argv=invalid_argv)
        )
        with pytest.raises(LateSignalRejected, match="invocation"):
            store.record_runtime_observation(
                replace(
                    draft,
                    native_event_id="invalid-validator-argv",
                    payload=replace(draft.payload, normalized_argv=invalid_argv),
                ),
                adapter_identity="trusted-adapter",
                binding_generation="generation-1",
            )
        private_target = "my complete private conversation goes here"
        private_argv = ("pytest", private_target)
        authority.register_validator_invocation(
            replace(invocation, normalized_argv=private_argv)
        )
        with pytest.raises(LateSignalRejected, match="invocation"):
            store.record_runtime_observation(
                replace(
                    draft,
                    native_event_id="private-validator-target",
                    comparison_key=replace(
                        draft.comparison_key, target_ref=private_target
                    ),
                    payload=replace(draft.payload, normalized_argv=private_argv),
                ),
                adapter_identity="trusted-adapter",
                binding_generation="generation-1",
            )
        persisted_payloads = " ".join(
            str(row[0]) for row in store._conn.execute("SELECT payload FROM events")
        )
        assert "tests/model_os" not in persisted_payloads
        assert private_target not in persisted_payloads
        assert "argv_sha256:" in persisted_payloads

        authority.register_reference(
            exit_ref,
            attempt_id="attempt-1",
            authority=EvidenceAuthority.RUNTIME_OBSERVATION,
        )
        authority.register_reference(
            output_ref,
            attempt_id="attempt-1",
            authority=EvidenceAuthority.RUNTIME_OBSERVATION,
        )
        authority.register_validator_invocation(invocation)
        with pytest.raises(LateSignalRejected, match="evidence_authority"):
            store.record_runtime_observation(
                replace(draft, native_event_id="wrong-evidence-authority"),
                adapter_identity="trusted-adapter",
                binding_generation="generation-1",
            )
    finally:
        store.close()


def test_store_rejects_unavailable_pending_and_causal_forgery(db_path) -> None:
    authority = authority_for()
    for ref_id in ("parent", "terminal", "pending"):
        authority.register_reference(
            EvidenceRef("cc", ref_id, runtime="cc", invocation_id="attempt-1")
        )
    store = _opened_store(db_path, authority)
    try:
        parent = store.record_runtime_observation(
            execution_draft(native_event_id="parent"),
            adapter_identity="trusted-adapter",
            binding_generation="generation-1",
        )
        terminal_ref = EvidenceRef(
            "cc", "terminal", runtime="cc", invocation_id="attempt-1"
        )
        terminal_base = execution_draft(
            native_event_id="terminal", evidence_ref=terminal_ref
        )
        terminal = replace(
            terminal_base,
            kind=SignalKind(SignalFamily.TURN_OUTCOME, "failure"),
            comparison_key=replace(
                terminal_base.comparison_key, attempt_category="turn:main"
            ),
            payload=TurnOutcomePayload(terminal_ref),
            causal_parent_ref=CausalLink(
                CausalRelation.DIRECT, parent.signal.signal_id
            ),
        )
        assert store.record_runtime_observation(
            terminal,
            adapter_identity="trusted-adapter",
            binding_generation="generation-1",
        ).inserted

        with pytest.raises(LateSignalRejected, match="causal_parent"):
            store.record_runtime_observation(
                replace(
                    terminal,
                    native_event_id="missing-parent",
                    causal_parent_ref=CausalLink(CausalRelation.DIRECT, "missing"),
                ),
                adapter_identity="trusted-adapter",
                binding_generation="generation-1",
            )

        pending_ref = EvidenceRef(
            "cc", "pending", runtime="cc", invocation_id="attempt-1"
        )
        pending_base = execution_draft(
            native_event_id="pending", evidence_ref=pending_ref
        )
        pending = replace(
            pending_base,
            kind=SignalKind(SignalFamily.EXECUTION_OBSERVATION, "pending_lost"),
            validity=PendingState("forged-terminal"),
            comparison_key=replace(
                pending_base.comparison_key,
                attempt_category="execution:pending_lost",
            ),
            payload=PendingLostPayload(
                "requires_reconcile",
                "unknown",
                "inspect_reality",
                native_binding_generation="generation-1",
            ),
        )
        with pytest.raises(LateSignalRejected, match="must_be_derived"):
            store.record_runtime_observation(
                pending,
                adapter_identity="trusted-adapter",
                binding_generation="generation-1",
            )

        with pytest.raises(LateSignalRejected, match="pending_binding_generation"):
            store.record_runtime_observation(
                replace(
                    pending,
                    native_event_id="old-pending-generation",
                    validity=PendingState(),
                    payload=replace(
                        pending.payload, native_binding_generation="old-generation"
                    ),
                ),
                adapter_identity="trusted-adapter",
                binding_generation="generation-1",
            )

        with pytest.raises(LateSignalRejected, match="effective_effort"):
            store.record_runtime_observation(
                replace(
                    terminal,
                    native_event_id="cc-effective-effort",
                    causal_parent_ref=None,
                    kind=SignalKind(SignalFamily.TURN_OUTCOME, "success"),
                    payload=TurnOutcomePayload(
                        terminal_ref,
                        requested_effort="high",
                        effective_effort="high",
                    ),
                ),
                adapter_identity="trusted-adapter",
                binding_generation="generation-1",
            )
    finally:
        store.close()


def test_pending_signal_closes_only_after_real_episode_resolution(
    db_path, monkeypatch
) -> None:
    clock = FakeClock()
    clock.install(monkeypatch)
    bootstrap = ModelOsStore(db_path)
    bootstrap.open()
    episode, lease, task, _ = make_running_task_episode(bootstrap)
    activate_episode(bootstrap, episode.episode_id, lease)
    bootstrap.suspend_episode(
        episode.episode_id,
        expected_lease_id=lease.lease_id,
        expected_owner=lease.owner,
        expected_token=lease.fencing_token,
        pending=PendingDescriptor(
            kind=WaitingSubtype.INPUT,
            native_generation="generation-1",
            correlation_id="corr-1",
            cause="need input",
            posed_at=clock.now_iso(),
        ),
    )
    bootstrap.mark_pending_channel_lost(
        episode.episode_id,
        reason=ReconcileReason.REQUIRES_USER_RESTART,
    )
    reconcile_event = next(
        event
        for _, event in reversed(bootstrap.list_events())
        if event.kind == EventKind.EPISODE_RECONCILE_REQUIRED
    )
    bootstrap.close()

    authority = authority_for(
        task_id=task.task_id,
        episode_id=episode.episode_id,
        binding_generation="generation-1",
    )
    evidence = EvidenceRef(
        "journal",
        reconcile_event.event_id,
        runtime="cc",
        invocation_id="attempt-1",
    )
    base = execution_draft(task_id=task.task_id, evidence_ref=evidence)
    draft = replace(
        base,
        observed_at=clock.now_iso(),
        subject=EpisodeRef(episode.episode_id),
        kind=SignalKind(SignalFamily.EXECUTION_OBSERVATION, "pending_lost"),
        validity=PendingState(),
        comparison_key=replace(
            base.comparison_key, attempt_category="execution:pending_lost"
        ),
        payload=PendingLostPayload(
            "requires_user_restart",
            "not_sent",
            "ask_user_again",
            native_binding_generation="generation-1",
        ),
    )
    store = _opened_store(db_path, authority)
    try:
        store.record_runtime_observation(
            draft,
            adapter_identity="trusted-adapter",
            binding_generation="generation-1",
        )
        before = store.signals_for_task(
            task.task_id, as_of="2026-07-22T00:00:00+00:00"
        ).items[0]
        assert isinstance(before.validity, PendingState)
        assert before.validity.terminal_event_ref is None

        store.resolve_reconcile(
            episode.episode_id, decision="close", confirmed_by="human-1"
        )
        after = store.signals_for_task(
            task.task_id, as_of="2026-07-22T00:00:00+00:00"
        ).items[0]
        assert isinstance(after.validity, PendingState)
        assert after.validity.terminal_event_ref is not None
    finally:
        store.close()


def test_taskless_attempt_cannot_borrow_task_journal_evidence(db_path) -> None:
    bootstrap = ModelOsStore(db_path)
    bootstrap.open()
    bootstrap.record_context_boundary(
        "session-other",
        task_id="other-task",
        episode_id=None,
        generation=1,
        trigger="compact",
        occurred_at="2026-07-25T00:00:00+00:00",
    )
    foreign_event = bootstrap.list_events()[-1][1]
    bootstrap.close()

    authority = authority_for(task_id=None, episode_id=None)
    evidence = EvidenceRef(
        "journal",
        foreign_event.event_id,
        runtime="cc",
        invocation_id="attempt-1",
    )
    store = _opened_store(db_path, authority)
    try:
        with pytest.raises(LateSignalRejected, match="cross_entity"):
            store.record_runtime_observation(
                execution_draft(task_id=None, evidence_ref=evidence),
                adapter_identity="trusted-adapter",
                binding_generation="generation-1",
            )
    finally:
        store.close()


def test_codex_retry_cannot_claim_unavailable_retry_metadata(db_path) -> None:
    authority = authority_for(runtime="codex")
    evidence = EvidenceRef(
        "codex", "retry-1", runtime="codex", invocation_id="attempt-1"
    )
    authority.register_reference(evidence)
    base = execution_draft(
        runtime="codex",
        native_event_id="retry-1",
        evidence_ref=evidence,
    )
    retry = replace(
        base,
        kind=SignalKind(SignalFamily.EXECUTION_OBSERVATION, "retry"),
        reliability=Reliability.WEAK,
        comparison_key=replace(
            base.comparison_key, attempt_category="execution:retry"
        ),
    )
    store = _opened_store(db_path, authority)
    try:
        assert store.record_runtime_observation(
            retry,
            adapter_identity="trusted-adapter",
            binding_generation="generation-1",
        ).inserted
        with pytest.raises(LateSignalRejected, match="runtime_retry_contract"):
            store.record_runtime_observation(
                replace(
                    retry,
                    native_event_id="retry-forged",
                    payload=replace(
                        retry.payload,
                        retry_attempt=9,
                        retry_max=9,
                        retry_delay_ms=123,
                    ),
                ),
                adapter_identity="trusted-adapter",
                binding_generation="generation-1",
            )
    finally:
        store.close()


def test_fenced_signal_rejects_wrong_owner_and_cross_task_binding(
    db_path, monkeypatch
) -> None:
    clock = FakeClock()
    clock.install(monkeypatch)
    bootstrap = ModelOsStore(db_path)
    bootstrap.open()
    episode, lease, task, _ = make_running_task_episode(bootstrap)
    bootstrap.close()

    authority = InMemorySignalAuthorityRegistry()
    authority.register_attempt(
        AttemptBinding(
            attempt_id="attempt-1",
            runtime="cc",
            task_id=task.task_id,
            episode_id=episode.episode_id,
            native_session_id="session-1",
            binding_generation="generation-1",
        )
    )
    ref = EvidenceRef("cc", "native-event-1", runtime="cc", invocation_id="attempt-1")
    authority.register_reference(ref)
    store = _opened_store(db_path, authority)
    try:
        draft = execution_draft(task_id=task.task_id, evidence_ref=ref)
        with pytest.raises(LateSignalRejected):
            store.record_fenced_episode_signal(
                draft,
                episode_id=episode.episode_id,
                expected_lease_id=lease.lease_id,
                expected_owner="wrong-owner",
                expected_token=lease.fencing_token,
            )
        clock.advance(301)
        current = store.acquire_episode_ownership(
            episode.episode_id, owner="new-owner", ttl_seconds=300
        )
        assert current.fencing_token > lease.fencing_token
        with pytest.raises(LateSignalRejected):
            store.record_fenced_episode_signal(
                draft,
                episode_id=episode.episode_id,
                expected_lease_id=lease.lease_id,
                expected_owner=lease.owner,
                expected_token=lease.fencing_token,
            )
        crossed = execution_draft(task_id="another-task", evidence_ref=ref)
        with pytest.raises(LateSignalRejected):
            store.record_fenced_episode_signal(
                crossed,
                episode_id=episode.episode_id,
                expected_lease_id=lease.lease_id,
                expected_owner=lease.owner,
                expected_token=lease.fencing_token,
            )
    finally:
        store.close()


def test_projection_paginates_rebuilds_and_is_visible_to_second_connection(db_path) -> None:
    authority = authority_for()
    for ordinal in range(3):
        authority.register_reference(
            EvidenceRef(
                "cc", f"event-{ordinal}", runtime="cc", invocation_id="attempt-1"
            )
        )
    store = _opened_store(db_path, authority)
    try:
        for ordinal in range(3):
            store.record_runtime_observation(
                execution_draft(native_event_id=f"event-{ordinal}"),
                adapter_identity="trusted-adapter",
                binding_generation="generation-1",
            )
        first = store.signals_for_task(
            "task-1", as_of="2026-07-25T02:00:00+00:00", limit=2
        )
        assert len(first.items) == 2
        assert first.next_cursor is not None
        second = store.signals_for_task(
            "task-1", as_of="2026-07-25T02:00:00+00:00", limit=2,
            cursor=first.next_cursor,
        )
        assert len(second.items) == 1
        before = [item.signal_id for item in (*first.items, *second.items)]

        other = sqlite3.connect(db_path)
        try:
            assert other.execute(
                "SELECT count(*) FROM cognitive_signal_projection"
            ).fetchone()[0] == 3
        finally:
            other.close()

        store._conn.execute("DROP TABLE cognitive_signal_projection")
        store._conn.commit()
        assert store.rebuild_cognitive_signal_projection() == 3
        rebuilt = store.signals_for_task(
            "task-1", as_of="2026-07-25T02:00:00+00:00", limit=10
        )
        assert [item.signal_id for item in rebuilt.items] == before
        snapshot = store.read_snapshot()
        assert EventKind.COGNITIVE_SIGNAL_RECORDED not in snapshot.unrecognized_event_kinds
        assert not hasattr(snapshot, "cognitive_signals")
    finally:
        store.close()


def test_structured_user_and_model_entries_assign_provenance(db_path) -> None:
    authority = authority_for()
    user_ref = EvidenceRef(
        "user_action", "action-1", runtime="cc", invocation_id="attempt-1"
    )
    model_ref = EvidenceRef(
        "cc", "message-1", runtime="cc", invocation_id="attempt-1"
    )
    authority.register_reference(
        user_ref,
        authority=EvidenceAuthority.STRUCTURED_USER_ACTION,
        action_subtype="correction",
    )
    authority.register_reference(
        model_ref, authority=EvidenceAuthority.NATIVE_MODEL_MESSAGE
    )
    store = _opened_store(db_path, authority)
    try:
        base = execution_draft(evidence_ref=user_ref)
        user = store.record_structured_user_signal(
            replace(
                base,
                kind=SignalKind(SignalFamily.USER_FEEDBACK, "correction"),
                payload=UserFeedbackPayload(user_ref),
            ),
            user_action_ref=user_ref,
        )
        model_base = execution_draft(
            native_event_id="message-1", evidence_ref=model_ref
        )
        model = store.record_model_report(
            replace(
                model_base,
                kind=SignalKind(SignalFamily.MODEL_REPORT, "uncertainty"),
                reliability=Reliability.WEAK,
                payload=ModelReportPayload(
                    native_message_ref=model_ref,
                    text_hash=hash_text("classified-correction"),
                    raw_kind="free_text_correction_guess",
                ),
            ),
            native_message_ref=model_ref,
        )
        assert user.signal.provenance is Provenance.USER_DECISION
        assert model.signal.provenance is Provenance.MODEL_HYPOTHESIS
        assert model.signal.reliability is Reliability.WEAK
    finally:
        store.close()


def test_structured_user_entry_rejects_weak_classifier_guess(db_path) -> None:
    authority = authority_for()
    user_ref = EvidenceRef(
        "user_action", "action-1", runtime="cc", invocation_id="attempt-1"
    )
    authority.register_reference(
        user_ref,
        authority=EvidenceAuthority.STRUCTURED_USER_ACTION,
        action_subtype="correction",
    )
    store = _opened_store(db_path, authority)
    try:
        draft = replace(
            execution_draft(evidence_ref=user_ref),
            kind=SignalKind(SignalFamily.USER_FEEDBACK, "correction"),
            reliability=Reliability.WEAK,
            payload=UserFeedbackPayload(user_ref),
        )
        with pytest.raises(LateSignalRejected):
            store.record_structured_user_signal(draft, user_action_ref=user_ref)
    finally:
        store.close()


def test_model_report_projection_redacts_secret_shaped_reference(db_path) -> None:
    authority = authority_for()
    secret_ref = EvidenceRef(
        "cc", "sk-secret-value-123456", runtime="cc", invocation_id="attempt-1"
    )
    authority.register_reference(
        secret_ref, authority=EvidenceAuthority.NATIVE_MODEL_MESSAGE
    )
    store = _opened_store(db_path, authority)
    try:
        base = execution_draft(evidence_ref=secret_ref)
        draft = replace(
            base,
            kind=SignalKind(SignalFamily.MODEL_REPORT, "uncertainty"),
            reliability=Reliability.WEAK,
            payload=ModelReportPayload(secret_ref, hash_text("message")),
        )
        store.record_model_report(draft, native_message_ref=secret_ref)
        raw = store._conn.execute(
            "SELECT signal_json FROM cognitive_signal_projection"
        ).fetchone()[0]
        assert "sk-secret-value" not in raw
        assert "sha256:" in raw
    finally:
        store.close()


def test_plaintext_cannot_hide_in_typed_evidence_refs(db_path) -> None:
    authority = authority_for()
    private_text = "my complete private conversation goes here"
    execution_ref = EvidenceRef(
        "cc", private_text, runtime="cc", invocation_id="attempt-1"
    )
    user_ref = EvidenceRef(
        "user_action", private_text, runtime="cc", invocation_id="attempt-1"
    )
    model_ref = EvidenceRef(
        "cc_model", private_text, runtime="cc", invocation_id="attempt-1"
    )
    authority.register_reference(execution_ref)
    authority.register_reference(
        user_ref,
        authority=EvidenceAuthority.STRUCTURED_USER_ACTION,
        action_subtype="correction",
    )
    authority.register_reference(
        model_ref, authority=EvidenceAuthority.NATIVE_MODEL_MESSAGE
    )
    store = _opened_store(db_path, authority)
    try:
        execution = execution_draft(
            native_event_id="private-execution", evidence_ref=execution_ref
        )
        store.record_runtime_observation(
            replace(
                execution,
                comparison_key=replace(
                    execution.comparison_key, target_ref=private_text
                ),
            ),
            adapter_identity="trusted-adapter",
            binding_generation="generation-1",
        )
        user_base = execution_draft(
            native_event_id="private-user", evidence_ref=user_ref
        )
        store.record_structured_user_signal(
            replace(
                user_base,
                kind=SignalKind(SignalFamily.USER_FEEDBACK, "correction"),
                payload=UserFeedbackPayload(user_ref),
            ),
            user_action_ref=user_ref,
        )
        model_base = execution_draft(
            native_event_id="private-model", evidence_ref=model_ref
        )
        store.record_model_report(
            replace(
                model_base,
                kind=SignalKind(SignalFamily.MODEL_REPORT, "uncertainty"),
                reliability=Reliability.WEAK,
                payload=ModelReportPayload(model_ref, hash_text(private_text)),
            ),
            native_message_ref=model_ref,
        )
        payloads = " ".join(
            str(row[0]) for row in store._conn.execute("SELECT payload FROM events")
        )
        assert private_text not in payloads
        assert hash_text(private_text) in payloads
    finally:
        store.close()


def test_unknown_future_signal_is_preserved_without_breaking_query(db_path) -> None:
    authority = authority_for()
    ref = EvidenceRef("cc", "event-1", runtime="cc", invocation_id="attempt-1")
    authority.register_reference(ref)
    store = _opened_store(db_path, authority)
    try:
        recorded = store.record_runtime_observation(
            execution_draft(native_event_id="event-1", evidence_ref=ref),
            adapter_identity="trusted-adapter",
            binding_generation="generation-1",
        )
        row = store._conn.execute(
            "SELECT payload FROM events WHERE seq=?", (recorded.event_seq,)
        ).fetchone()
        payload = json.loads(row["payload"])
        payload["signal"]["signal_id"] = "future-signal"
        payload["signal"]["kind"] = {
            "family": "future_family",
            "subtype": "future_subtype",
        }
        payload["signal"]["native_event_id"] = "future-event"
        payload = redact_payload(payload)
        insert_projection_row(store._conn, event_seq=recorded.event_seq + 100, event_payload=payload)
        store._conn.commit()
        page = store.signals_for_task(
            "task-1", as_of="2026-07-25T02:00:00+00:00", limit=10
        )
        assert isinstance(page.items[-1], UnknownCognitiveSignal)
        assert page.items[-1].raw_payload["payload"]["type"] == "execution_outcome"
    finally:
        store.close()
