"""Fault-injection tests (slice-084 pass criterion 3 + testing method).

"写入中断不会留下半个决定或重复 seq": a crash mid-transaction must not leave a
half-written decision/event pair or a duplicated seq.

We trigger real mid-transaction failures via SQLite's own UNIQUE constraints
(pre-seeding a duplicate event_id) rather than monkeypatching the C-level
``sqlite3.Connection.execute`` (which is read-only). This exercises the
actual atomicity path: the store's ``with conn:`` transaction block must
roll back every statement when any one of them fails.
"""

from __future__ import annotations

import sqlite3

import pytest

from trowel_py.model_os.store import ModelOsStore
from trowel_py.model_os.types import (
    DecisionRecord,
    EventEnvelope,
    EventKind,
    Provenance,
)


def _note(event_id: str) -> EventEnvelope:
    return EventEnvelope(
        event_id=event_id,
        kind=EventKind.NOTE,
        occurred_at="2026-07-21T00:00:00Z",
        source="test",
        provenance=Provenance.MACHINE_OBSERVATION,
        policy_version="v0",
        payload={"i": 1},
    )


def _decision(decision_id: str) -> DecisionRecord:
    return DecisionRecord(
        decision_id=decision_id,
        kind="route",
        decided_at="2026-07-21T00:00:00Z",
        signals={"usage_ratio": 0.8},
        candidates=["fast", "deep"],
        choice="deep",
        reason="validator failed",
        policy_version="v0",
    )


def test_crash_during_atomic_pair_leaves_nothing(store: ModelOsStore) -> None:
    """append_decision_with_intent is atomic: if the event insert fails after
    the decision insert ran, neither row must survive (no half decision).

    The event insert is forced to fail by pre-seeding its event_id; the
    decision insert runs first inside the same ``with conn:`` block, then the
    duplicate event insert raises, and the whole transaction rolls back.
    """

    store.append_event(_note("evt-crash"))  # occupy the event_id in its own txn
    baseline_events = len(store.list_events())
    baseline_decisions = len(store.list_decisions())

    with pytest.raises(sqlite3.IntegrityError):
        store.append_decision_with_intent(
            _decision("dec-crash"),
            _note("evt-crash"),  # duplicate event_id → IntegrityError
        )

    assert len(store.list_events()) == baseline_events
    assert len(store.list_decisions()) == baseline_decisions


def test_crash_does_not_advance_seq(store: ModelOsStore) -> None:
    """A rolled-back transaction must not consume a seq (no gap, no duplicate)."""

    store.append_event(_note("evt-crash"))  # seq 1
    with pytest.raises(sqlite3.IntegrityError):
        store.append_decision_with_intent(
            _decision("dec-crash"),
            _note("evt-crash"),  # duplicate → rollback, dec-crash lost
        )

    # dec-crash was rolled back, so the next committed decision is seq 1
    # (not 2). The next event after evt-crash is seq 2.
    d_seq, e_seq = store.append_decision_with_intent(
        _decision("dec-ok"), _note("evt-ok")
    )
    assert d_seq == 1
    assert e_seq == 2


def test_successful_atomic_pair_commits_both(store: ModelOsStore) -> None:
    """The happy path: decision + intent event land together."""

    d_seq, e_seq = store.append_decision_with_intent(
        _decision("dec-ok"), _note("evt-ok")
    )
    assert d_seq >= 1
    assert e_seq >= 1
    assert len(store.list_decisions()) == 1
    assert len(store.list_events()) == 1
