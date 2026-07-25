from __future__ import annotations

from dataclasses import replace

import pytest

from trowel_py.model_os.journal import JournalIdentityConflict
from trowel_py.model_os.store import ModelOsStore
from trowel_py.model_os.types import (
    DecisionDisposition,
    DecisionRecord,
    EventEnvelope,
    EventKind,
    Provenance,
)


def _decision(
    decision_id: str = "decision.route",
    *,
    disposition: DecisionDisposition = DecisionDisposition.EXECUTE,
) -> DecisionRecord:
    return DecisionRecord(
        decision_id=decision_id,
        kind="route",
        disposition=disposition,
        decided_at="2026-07-25T00:00:00Z",
        signals={"refs": ["event.context.high"]},
        candidates=["fast", "deep"],
        choice="deep",
        reason="context_high",
        policy_version="router-v1",
        correlation_id=(
            "command.route" if disposition == DecisionDisposition.EXECUTE else None
        ),
    )


def _intent(
    *,
    event_id: str = "event.command.intent",
    cause_id: str = "decision.route",
    correlation_id: str = "command.route",
) -> EventEnvelope:
    return EventEnvelope(
        event_id=event_id,
        kind=EventKind.COMMAND_INTENT,
        occurred_at="2026-07-25T00:00:01Z",
        source="kernel",
        provenance=Provenance.MACHINE_OBSERVATION,
        policy_version="router-v1",
        payload={
            "command_kind": "route.select",
            "target_ref": "episode.1",
            "idempotency_key_hash": "sha256:idem123",
            "args_hash": "sha256:args123",
        },
        cause_id=cause_id,
        correlation_id=correlation_id,
    )


def test_execute_pair_retries_only_when_both_identities_match(
    store: ModelOsStore,
) -> None:
    decision = _decision()
    intent = _intent()
    original = store.append_decision_with_intent(decision, intent)

    assert store.append_decision_with_intent(
        replace(decision, decided_at="2026-07-25T00:05:00Z"),
        replace(intent, occurred_at="2026-07-25T00:05:01Z"),
    ) == original

    with pytest.raises(JournalIdentityConflict):
        store.append_decision_with_intent(
            replace(decision, choice="fast"),
            intent,
        )
    with pytest.raises(JournalIdentityConflict):
        store.append_decision_with_intent(
            decision,
            replace(intent, payload={**intent.payload, "target_ref": "episode.2"}),
        )
    with pytest.raises(JournalIdentityConflict):
        store.append_decision_with_intent(
            replace(decision, correlation_id="command.changed"),
            replace(intent, correlation_id="command.changed"),
        )


def test_execute_requires_atomic_intent_and_non_execute_forbids_it(
    store: ModelOsStore,
) -> None:
    with pytest.raises(ValueError):
        store.append_decision(_decision())
    with pytest.raises(ValueError):
        store.append_event(_intent())
    with pytest.raises(ValueError):
        store.append_decision_with_intent(
            _decision(disposition=DecisionDisposition.NO_ACTION),
            _intent(),
        )


@pytest.mark.parametrize(
    "intent",
    [
        _intent(cause_id="decision.other"),
        _intent(correlation_id="command.other"),
        replace(_intent(), kind=EventKind.NOTE),
        replace(_intent(), payload={**_intent().payload, "raw_args": "private"}),
        replace(_intent(), payload={**_intent().payload, "target_ref": "private body"}),
    ],
)
def test_execute_pair_rejects_broken_link_or_payload(
    store: ModelOsStore, intent: EventEnvelope
) -> None:
    with pytest.raises(ValueError):
        store.append_decision_with_intent(_decision(), intent)


def test_terminal_command_payload_uses_allowlist(store: ModelOsStore) -> None:
    store.append_decision_with_intent(_decision(), _intent())
    result = EventEnvelope(
        event_id="event.command.result",
        kind=EventKind.COMMAND_RESULT,
        occurred_at="2026-07-25T00:00:02Z",
        source="runner",
        provenance=Provenance.MACHINE_OBSERVATION,
        policy_version="router-v1",
        payload={
            "result_code": "succeeded",
            "evidence_refs": ["evidence.test.report"],
            "usage_ref": "usage.1",
        },
        cause_id="decision.route",
        correlation_id="command.route",
    )
    seq = store.append_event(result)
    assert store.append_event(replace(result, occurred_at="2026-07-25T00:03:00Z")) == seq

    with pytest.raises(ValueError):
        store.append_event(
            replace(result, event_id="event.bad", payload={**result.payload, "log": "raw"})
        )


def test_command_events_are_known_audit_events(store: ModelOsStore) -> None:
    store.append_decision_with_intent(_decision(), _intent())
    store.append_event(
        EventEnvelope(
            event_id="event.command.unknown",
            kind=EventKind.COMMAND_UNKNOWN,
            occurred_at="2026-07-25T00:00:02Z",
            source="runner",
            provenance=Provenance.MACHINE_OBSERVATION,
            policy_version="router-v1",
            payload={
                "unknown_code": "unknown_requires_reconcile",
                "evidence_refs": [],
            },
            cause_id="decision.route",
            correlation_id="command.route",
        )
    )

    assert store.replay().unrecognized_event_kinds == ()
