from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

import pytest

from trowel_py.model_os.explain import DecisionNotFound
from trowel_py.model_os.store import ModelOsStore
from trowel_py.model_os.types import (
    DecisionDisposition,
    DecisionRecord,
    EventEnvelope,
    EventKind,
    Provenance,
)


GOLDEN_PATH = (
    Path(__file__).resolve().parents[3]
    / "spikes/model-os-observation-20260723/golden-explain.json"
)


def _decision(case: dict[str, object]) -> DecisionRecord:
    return DecisionRecord(
        decision_id=str(case["decision_id"]),
        kind=str(case["decision_kind"]),
        disposition=DecisionDisposition(str(case["disposition"])),
        decided_at="2026-07-25T00:00:00Z",
        signals={"refs": list(case["signal_refs"])},
        candidates=list(case["candidates"]),
        choice=str(case["choice"]),
        reason=str(case["reason_code"]),
        policy_version=str(case["policy_version"]),
        budget_before=case["budget_before"],
        budget_after=case["budget_after"],
        correlation_id=case["command"]["correlation_id"],
    )


def _command_event(
    *,
    event_id: str,
    kind: str,
    decision_id: str,
    correlation_id: str,
    payload: dict[str, object],
) -> EventEnvelope:
    return EventEnvelope(
        event_id=event_id,
        kind=kind,
        occurred_at="2026-07-25T00:00:01Z",
        source="test",
        provenance=Provenance.MACHINE_OBSERVATION,
        policy_version="v1",
        payload=payload,
        cause_id=decision_id,
        correlation_id=correlation_id,
    )


def _persist_legacy(store: ModelOsStore, case: dict[str, object]) -> None:
    assert store._conn is not None
    store._conn.execute(
        "INSERT INTO decisions (decision_id, kind, disposition, decided_at, "
        "policy_version, signals, candidates, choice, reason, budget_before, "
        "budget_after, identity_hash) VALUES (?, ?, 'legacy_unknown', ?, ?, ?, ?, "
        "?, ?, NULL, NULL, 'sha256:legacy')",
        (
            case["decision_id"],
            case["decision_kind"],
            "2026-07-25T00:00:00Z",
            case["policy_version"],
            '{"prompt":"private legacy body"}',
            '["private legacy candidate"]',
            "private legacy choice",
            "private legacy reason",
        ),
    )
    store._conn.commit()


def _persist_case(store: ModelOsStore, case: dict[str, object]) -> None:
    if case["disposition"] == "legacy_unknown":
        _persist_legacy(store, case)
        return
    decision = _decision(case)
    command = case["command"]
    correlation_id = command["correlation_id"]
    if decision.disposition != DecisionDisposition.EXECUTE:
        store.append_decision(decision)
        return
    assert isinstance(correlation_id, str)
    intent = _command_event(
        event_id=str(command["intent_event_id"]),
        kind=EventKind.COMMAND_INTENT,
        decision_id=decision.decision_id,
        correlation_id=correlation_id,
        payload={
            "command_kind": "runtime.execute",
            "target_ref": "episode.1",
            "idempotency_key_hash": "sha256:idem123",
            "args_hash": "sha256:args123",
        },
    )
    store.append_decision_with_intent(decision, intent)
    status = command["status"]
    terminal_id = command["terminal_event_id"]
    if status == "pending":
        return
    if status == "inconsistent":
        terminal_ids = command["conflict_event_ids"]
        store.append_event(
            _command_event(
                event_id=str(terminal_ids[0]),
                kind=EventKind.COMMAND_RESULT,
                decision_id=decision.decision_id,
                correlation_id=correlation_id,
                payload={"result_code": "succeeded", "evidence_refs": []},
            )
        )
        store.append_event(
            _command_event(
                event_id=str(terminal_ids[1]),
                kind=EventKind.COMMAND_UNKNOWN,
                decision_id=decision.decision_id,
                correlation_id=correlation_id,
                payload={
                    "unknown_code": "unknown_requires_reconcile",
                    "evidence_refs": [],
                },
            )
        )
        return
    assert isinstance(terminal_id, str)
    if status in {"succeeded", "failed"}:
        terminal = _command_event(
            event_id=terminal_id,
            kind=EventKind.COMMAND_RESULT,
            decision_id=decision.decision_id,
            correlation_id=correlation_id,
            payload={
                "result_code": status,
                "evidence_refs": list(command["evidence_refs"]),
            },
        )
    else:
        terminal = _command_event(
            event_id=terminal_id,
            kind=EventKind.COMMAND_UNKNOWN,
            decision_id=decision.decision_id,
            correlation_id=correlation_id,
            payload={
                "unknown_code": status,
                "evidence_refs": list(command["evidence_refs"]),
            },
        )
    store.append_event(terminal)


def test_nine_golden_explanations_match_exactly(store: ModelOsStore) -> None:
    golden = json.loads(GOLDEN_PATH.read_text())
    for case in golden:
        _persist_case(store, case)

    actual = [asdict(store.explain_decision(case["decision_id"])) for case in golden]

    assert actual == golden


def test_boundary_hides_future_terminal_and_future_decision(
    store: ModelOsStore,
) -> None:
    golden = json.loads(GOLDEN_PATH.read_text())
    pending = next(case for case in golden if case["command"]["status"] == "pending")
    _persist_case(store, pending)
    boundary = store.journal_boundary()
    decision = _decision(pending)
    store.append_event(
        _command_event(
            event_id="event.command.result.future",
            kind=EventKind.COMMAND_RESULT,
            decision_id=decision.decision_id,
            correlation_id=str(decision.correlation_id),
            payload={"result_code": "succeeded", "evidence_refs": []},
        )
    )
    later = DecisionRecord(
        decision_id="decision.future",
        kind="route",
        disposition=DecisionDisposition.NO_ACTION,
        decided_at="2020-01-01T00:00:00Z",
        signals={"refs": []},
        candidates=["fast"],
        choice="fast",
        reason="default_fast",
        policy_version="v1",
    )
    store.append_decision(later)

    assert store.explain_decision(decision.decision_id, boundary=boundary).command.status == "pending"
    assert store.explain_decision(decision.decision_id).command.status == "succeeded"
    with pytest.raises(DecisionNotFound):
        store.explain_decision(later.decision_id, boundary=boundary)
    with pytest.raises(DecisionNotFound):
        store.explain_decision("decision.missing")
