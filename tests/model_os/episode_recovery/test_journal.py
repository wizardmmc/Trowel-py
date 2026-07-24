from __future__ import annotations

from tests.model_os._episode_helpers import make_cooperative_snapshot
from tests.model_os.episode_recovery.support import build, side_effect_event
from trowel_py.model_os.types import (
    EventEnvelope,
    EventKind,
    Provenance,
    SideEffectRecord,
)


def test_recovery_folds_done_side_effects_from_journal() -> None:
    event = side_effect_event(
        action_ref="action/send-message",
        outcome="done",
        evidence_ref="evidence/delivery.txt",
    )
    snapshot = build(
        prev=make_cooperative_snapshot(),
        journal_through_seq=99,
        events=(event,),
    )
    folded = [
        side_effect
        for side_effect in snapshot.side_effects
        if side_effect.action_ref == "action/send-message"
    ]
    assert len(folded) == 1
    assert folded[0].outcome == "done"
    assert folded[0].evidence_ref == "evidence/delivery.txt"
    assert (
        "action/send-message",
        "evidence/delivery.txt",
    ) in snapshot.completed_with_evidence


def test_recovery_preserves_unconfirmed_side_effects_as_unknown() -> None:
    event = side_effect_event(
        action_ref="action/pending-charge",
        outcome="unknown_requires_reconcile",
    )
    snapshot = build(
        prev=make_cooperative_snapshot(),
        journal_through_seq=5,
        events=(event,),
    )
    by_ref = {
        side_effect.action_ref: side_effect for side_effect in snapshot.side_effects
    }
    assert by_ref[event.payload["action_ref"]].outcome == "unknown_requires_reconcile"
    assert all(
        action_ref != event.payload["action_ref"]
        for action_ref, _ in snapshot.completed_with_evidence
    )


def test_recovery_does_not_double_count_action_already_in_prev() -> None:
    done = SideEffectRecord(
        action_ref="action/x",
        idempotency_key="key-x",
        outcome="done",
        evidence_ref="evidence/x",
    )
    duplicate = side_effect_event(
        action_ref="action/x",
        outcome="done",
        evidence_ref="evidence/x",
    )
    snapshot = build(
        prev=make_cooperative_snapshot(side_effects=(done,)),
        journal_through_seq=10,
        events=(duplicate,),
    )
    assert (
        sum(
            side_effect.action_ref == "action/x"
            for side_effect in snapshot.side_effects
        )
        == 1
    )


def test_recovery_ignores_non_side_effect_journal_events() -> None:
    note = EventEnvelope(
        event_id="note.1",
        kind=EventKind.NOTE,
        occurred_at="2026-07-21T00:00:07Z",
        source="runner",
        provenance=Provenance.MACHINE_OBSERVATION,
        policy_version="v0",
        payload={"message": "处理中"},
        episode_id="ep-1",
    )
    prev = make_cooperative_snapshot()
    snapshot = build(
        prev=prev,
        journal_through_seq=8,
        events=(note,),
    )
    assert snapshot.side_effects == prev.side_effects


def test_done_event_requires_action_and_evidence() -> None:
    no_action = side_effect_event(
        action_ref="",
        outcome="done",
        evidence_ref="evidence/ignored",
    )
    no_evidence = side_effect_event(
        action_ref="action/no-evidence",
        outcome="done",
    )
    snapshot = build(
        prev=make_cooperative_snapshot(side_effects=()),
        events=(no_action, no_evidence),
    )
    assert snapshot.side_effects == ()
    assert snapshot.completed_with_evidence == (
        ("action/read-config", "evidence/config-dump.txt"),
    )


def test_done_event_replaces_unknown_and_keeps_group_order() -> None:
    unknown_a = SideEffectRecord(
        action_ref="action/a",
        idempotency_key="unknown-a",
        outcome="unknown_requires_reconcile",
    )
    unknown_b = SideEffectRecord(
        action_ref="action/b",
        idempotency_key="unknown-b",
        outcome="unknown_requires_reconcile",
    )
    done_a = side_effect_event(
        action_ref="action/a",
        outcome="done",
        evidence_ref="evidence/a",
    )
    new_unknown = side_effect_event(
        action_ref="action/c",
        outcome="unknown_requires_reconcile",
    )
    snapshot = build(
        prev=make_cooperative_snapshot(side_effects=(unknown_a, unknown_b)),
        events=(new_unknown, done_a),
    )
    assert [
        (side_effect.action_ref, side_effect.outcome)
        for side_effect in snapshot.side_effects
    ] == [
        ("action/a", "done"),
        ("action/b", "unknown_requires_reconcile"),
        ("action/c", "unknown_requires_reconcile"),
    ]


def test_unknown_event_never_replaces_done() -> None:
    done = SideEffectRecord(
        action_ref="action/a",
        idempotency_key="done-a",
        outcome="done",
        evidence_ref="evidence/a",
    )
    unknown = side_effect_event(
        action_ref="action/a",
        outcome="unknown_requires_reconcile",
    )
    snapshot = build(
        prev=make_cooperative_snapshot(side_effects=(done,)),
        events=(unknown,),
    )
    assert snapshot.side_effects == (done,)
