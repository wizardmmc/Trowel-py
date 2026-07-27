"""Decision 与 command 结构事件的最小可追溯解释。"""

from __future__ import annotations

from dataclasses import dataclass
from collections.abc import Callable
import sqlite3
from typing import Any

from trowel_py.model_os.types import DecisionDisposition, DecisionRecord, EventEnvelope
from trowel_py.model_os.journal import (
    JournalBoundary,
    public_journal_label,
    public_journal_ref,
    validate_decision,
)
from trowel_py.model_os.redaction import redact_payload


class DecisionNotFound(LookupError):
    def __init__(self) -> None:
        super().__init__("decision not found at journal boundary")


@dataclass(frozen=True)
class CommandExplanation:
    correlation_id: str | None
    intent_event_id: str | None
    terminal_event_id: str | None
    status: str
    evidence_refs: list[str]
    conflict_event_ids: list[str]


@dataclass(frozen=True)
class DecisionExplanation:
    decision_id: str
    decision_kind: str
    disposition: str
    policy_version: str
    signal_refs: list[str]
    candidates: list[Any]
    choice: str
    reason_code: str
    budget_before: dict[str, Any] | None
    budget_after: dict[str, Any] | None
    command: CommandExplanation


def _command(
    *,
    correlation_id: str | None,
    intent_event_id: str | None = None,
    terminal_event_id: str | None = None,
    status: str,
    evidence_refs: list[str] | None = None,
    conflict_event_ids: list[str] | None = None,
) -> CommandExplanation:
    return CommandExplanation(
        correlation_id=public_journal_ref(correlation_id),
        intent_event_id=public_journal_ref(intent_event_id),
        terminal_event_id=public_journal_ref(terminal_event_id),
        status=public_journal_label(status),
        evidence_refs=[
            public_journal_ref(value) or "unknown" for value in (evidence_refs or [])
        ],
        conflict_event_ids=[
            public_journal_ref(value) or "unknown"
            for value in (conflict_event_ids or [])
        ],
    )


def _legacy_explanation(decision: DecisionRecord) -> DecisionExplanation:
    return DecisionExplanation(
        decision_id=public_journal_ref(decision.decision_id) or "unknown",
        decision_kind=public_journal_label(decision.kind),
        disposition=DecisionDisposition.LEGACY_UNKNOWN.value,
        policy_version=public_journal_label(decision.policy_version),
        signal_refs=[],
        candidates=[],
        choice="unknown",
        reason_code="legacy_unavailable",
        budget_before=None,
        budget_after=None,
        command=_command(correlation_id=None, status="legacy_unknown"),
    )


def explain_decision_record(
    decision: DecisionRecord,
    command_events: list[tuple[int, EventEnvelope]],
) -> DecisionExplanation:
    if decision.disposition == DecisionDisposition.LEGACY_UNKNOWN:
        return _legacy_explanation(decision)
    try:
        validate_decision(decision)
    except (TypeError, ValueError):
        return _legacy_explanation(decision)

    disposition = decision.disposition.value
    related = [event for _, event in sorted(command_events, key=lambda pair: pair[0])]
    if decision.disposition in {
        DecisionDisposition.NO_ACTION,
        DecisionDisposition.SHADOW,
    }:
        command = (
            _command(correlation_id=None, status="not_applicable")
            if not related
            else _command(
                correlation_id=related[0].correlation_id,
                status="inconsistent",
                conflict_event_ids=[event.event_id for event in related],
            )
        )
    else:
        correlation_id = decision.correlation_id
        valid = [
            event
            for event in related
            if event.cause_id == decision.decision_id
            and event.correlation_id == correlation_id
        ]
        invalid = [event for event in related if event not in valid]
        intents = [event for event in valid if event.kind == "command.intent"]
        terminals = [
            event
            for event in valid
            if event.kind in {"command.result", "command.unknown"}
        ]
        if len(intents) != 1 or invalid:
            conflicts = invalid + (intents if len(intents) > 1 else []) + terminals
            command = _command(
                correlation_id=correlation_id,
                intent_event_id=intents[0].event_id if len(intents) == 1 else None,
                status="inconsistent",
                conflict_event_ids=list(dict.fromkeys(event.event_id for event in conflicts)),
            )
        elif len(terminals) > 1:
            command = _command(
                correlation_id=correlation_id,
                intent_event_id=intents[0].event_id,
                status="inconsistent",
                conflict_event_ids=[event.event_id for event in terminals],
            )
        elif not terminals:
            command = _command(
                correlation_id=correlation_id,
                intent_event_id=intents[0].event_id,
                status="pending",
            )
        else:
            terminal = terminals[0]
            evidence_refs = list(terminal.payload.get("evidence_refs", []))
            if terminal.kind == "command.unknown":
                status = str(terminal.payload["unknown_code"])
            else:
                status = (
                    "succeeded"
                    if terminal.payload.get("result_code") == "succeeded"
                    else "failed"
                )
            command = _command(
                correlation_id=correlation_id,
                intent_event_id=intents[0].event_id,
                terminal_event_id=terminal.event_id,
                status=status,
                evidence_refs=evidence_refs,
            )

    return DecisionExplanation(
        decision_id=public_journal_ref(decision.decision_id) or "unknown",
        decision_kind=public_journal_label(decision.kind),
        disposition=disposition,
        policy_version=public_journal_label(decision.policy_version),
        signal_refs=[
            public_journal_ref(value) or "unknown"
            for value in decision.signals["refs"]
        ],
        candidates=redact_payload(list(decision.candidates)),
        choice=public_journal_label(decision.choice),
        reason_code=public_journal_label(decision.reason),
        budget_before=redact_payload(decision.budget_before),
        budget_after=redact_payload(decision.budget_after),
        command=command,
    )


def read_decision_explanation(
    conn: sqlite3.Connection,
    *,
    decision_id: str,
    boundary: JournalBoundary,
    decode_decision: Callable[[sqlite3.Row], DecisionRecord],
    decode_event: Callable[[sqlite3.Row], EventEnvelope],
) -> DecisionExplanation:
    row = conn.execute(
        "SELECT * FROM decisions WHERE decision_id=? AND seq <= ?",
        (decision_id, boundary.decision_seq),
    ).fetchone()
    if row is None:
        raise DecisionNotFound()
    decision = decode_decision(row)
    if decision.disposition == DecisionDisposition.LEGACY_UNKNOWN:
        return explain_decision_record(decision, [])
    if decision.correlation_id is None:
        event_rows = conn.execute(
            "SELECT * FROM events WHERE seq <= ? AND cause_id=? "
            "AND kind IN ('command.intent', 'command.result', 'command.unknown') "
            "ORDER BY seq",
            (boundary.event_seq, decision_id),
        ).fetchall()
    else:
        event_rows = conn.execute(
            "SELECT * FROM events WHERE seq <= ? "
            "AND (cause_id=? OR correlation_id=?) "
            "AND kind IN ('command.intent', 'command.result', 'command.unknown') "
            "ORDER BY seq",
            (boundary.event_seq, decision_id, decision.correlation_id),
        ).fetchall()
    events = [(int(item["seq"]), decode_event(item)) for item in event_rows]
    return explain_decision_record(decision, events)
