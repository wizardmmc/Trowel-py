"""Store-level behaviour for the Model OS journal (slice-084).

Covers the spec's pass criteria 1, 3, 5, 7, 8:
- bootstrap on a missing db (5)
- append event / decision with idempotent event_id and monotonic seq (3)
- replay by seq yields a deterministic snapshot (1)
- "action may have happened, result not written" replays as unknown and is
  never auto-replayed (7)
- Task / default / incubation / maintenance all create legal WorkItems, and
  system work never appears among task-kind work items (8)
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from trowel_py.model_os.store import ModelOsStore
from trowel_py.model_os.types import (
    DecisionRecord,
    EventEnvelope,
    EventKind,
    MemoryEligibility,
    Provenance,
    SessionPurpose,
    WorkItemKind,
    WorkItemStatus,
)


# ---------------------------------------------------------------- bootstrap ---


def test_open_bootstraps_missing_db(db_path: Path) -> None:
    """Opening a path that does not yet exist must create the schema cleanly."""

    assert not db_path.exists()
    store = ModelOsStore(db_path)
    store.open()
    assert db_path.exists()

    tables = {
        row["name"]
        for row in store._conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    # work_items are event-sourced (derived by the reducer from events), so
    # there is no mutable work_items table; snapshots are a future checkpoint
    # optimisation and intentionally absent in slice-084.
    for required in ("events", "decisions", "leases", "meta"):
        assert required in tables, f"missing table {required}"

    snap = store.read_snapshot()
    assert snap.schema_version >= 1


def test_open_idempotent_on_existing_db(store: ModelOsStore) -> None:
    """Re-opening an already-bootstrapped db preserves prior work items."""

    created = store.create_work_item(
        kind=WorkItemKind.TASK,
        owner_ref="user",
        task_id="task-1",
        session_purpose=SessionPurpose.FOREGROUND,
        memory_eligibility=MemoryEligibility.ELIGIBLE,
    )
    store.close()

    reopened = ModelOsStore(store.path)
    reopened.open()
    snap = reopened.read_snapshot()
    assert any(wi.work_item_id == created.work_item_id for wi in snap.work_items)


# --------------------------------------------------------------- work items ---


@pytest.mark.parametrize(
    "kind,owner,task_id,purpose,eligibility",
    [
        (WorkItemKind.TASK, "user", "task-A",
         SessionPurpose.FOREGROUND, MemoryEligibility.ELIGIBLE),
        (WorkItemKind.DEFAULT, "system", None,
         SessionPurpose.DEFAULT, MemoryEligibility.INELIGIBLE),
        (WorkItemKind.INCUBATION, "system", "task-A",
         SessionPurpose.INCUBATION, MemoryEligibility.INELIGIBLE),
        (WorkItemKind.MAINTENANCE, "system", None,
         SessionPurpose.MAINTENANCE, MemoryEligibility.INELIGIBLE),
        (WorkItemKind.EXPERIMENT, "system", None,
         SessionPurpose.EXPERIMENT, MemoryEligibility.INELIGIBLE),
    ],
)
def test_all_work_item_kinds_are_legal(
    store: ModelOsStore,
    kind: WorkItemKind,
    owner: str,
    task_id: str | None,
    purpose: SessionPurpose,
    eligibility: MemoryEligibility,
) -> None:
    """Task / default / incubation / maintenance / experiment all create
    legal WorkItems (spec pass criterion 8)."""

    wi = store.create_work_item(
        kind=kind,
        owner_ref=owner,
        task_id=task_id,
        session_purpose=purpose,
        memory_eligibility=eligibility,
    )
    assert wi.kind == kind
    assert wi.task_id == task_id
    assert wi.session_purpose == purpose
    assert wi.memory_eligibility == eligibility
    assert wi.status == WorkItemStatus.PENDING

    snap = store.read_snapshot()
    assert any(w.work_item_id == wi.work_item_id for w in snap.work_items)


def test_system_work_excluded_from_task_set(store: ModelOsStore) -> None:
    """default/maintenance/experiment must never appear among task work items."""

    store.create_work_item(
        kind=WorkItemKind.TASK,
        owner_ref="user",
        task_id="task-A",
        session_purpose=SessionPurpose.FOREGROUND,
        memory_eligibility=MemoryEligibility.ELIGIBLE,
    )
    store.create_work_item(
        kind=WorkItemKind.DEFAULT,
        owner_ref="system",
        task_id=None,
        session_purpose=SessionPurpose.DEFAULT,
        memory_eligibility=MemoryEligibility.INELIGIBLE,
    )
    store.create_work_item(
        kind=WorkItemKind.MAINTENANCE,
        owner_ref="system",
        task_id=None,
        session_purpose=SessionPurpose.MAINTENANCE,
        memory_eligibility=MemoryEligibility.INELIGIBLE,
    )

    snap = store.read_snapshot()
    tasks = snap.task_work_items()
    assert all(w.kind == WorkItemKind.TASK for w in tasks)
    assert len(tasks) == 1
    assert tasks[0].task_id == "task-A"


# -------------------------------------------------------- append / idempotent ---


def test_append_event_assigns_monotonic_seq(store: ModelOsStore) -> None:
    """seq strictly increases across appends and never duplicates."""

    seqs = []
    for i in range(3):
        ev = EventEnvelope(
            event_id=f"evt-{i}",
            kind=EventKind.NOTE,
            occurred_at="2026-07-21T00:00:00Z",
            source="test",
            provenance=Provenance.MACHINE_OBSERVATION,
            policy_version="v0",
            payload={"i": i},
        )
        seqs.append(store.append_event(ev))
    assert seqs == sorted(seqs)
    assert len(set(seqs)) == 3


def test_append_event_is_idempotent_on_event_id(store: ModelOsStore) -> None:
    """Inserting the same event_id twice does not duplicate or bump seq."""

    ev = EventEnvelope(
        event_id="evt-dup",
        kind=EventKind.NOTE,
        occurred_at="2026-07-21T00:00:00Z",
        source="test",
        provenance=Provenance.MACHINE_OBSERVATION,
        policy_version="v0",
        payload={"x": 1},
    )
    first = store.append_event(ev)
    second = store.append_event(ev)
    assert first == second
    assert len(store.list_events()) == 1


def test_append_decision_persists_and_replays(store: ModelOsStore) -> None:
    """A decision lands in the journal and is readable back."""

    decision = DecisionRecord(
        decision_id="dec-1",
        kind="route",
        decided_at="2026-07-21T00:00:00Z",
        signals={"usage_ratio": 0.82},
        candidates=["fast", "deep"],
        choice="deep",
        reason="validator failed twice",
        policy_version="v0",
        budget_before={"tokens": 1000},
        budget_after={"tokens": 4000},
    )
    seq = store.append_decision(decision)
    assert seq >= 1

    decisions = store.list_decisions()
    assert len(decisions) == 1
    assert decisions[0][1].decision_id == "dec-1"
    assert decisions[0][1].choice == "deep"


# --------------------------------------------------------------- replay (1) ---


def test_replay_twice_yields_equal_snapshot(store: ModelOsStore) -> None:
    """Spec pass criterion 1: same stream replayed twice → equal snapshots."""

    store.create_work_item(
        kind=WorkItemKind.TASK,
        owner_ref="user",
        task_id="task-A",
        session_purpose=SessionPurpose.FOREGROUND,
        memory_eligibility=MemoryEligibility.ELIGIBLE,
    )
    store.create_work_item(
        kind=WorkItemKind.DEFAULT,
        owner_ref="system",
        task_id=None,
        session_purpose=SessionPurpose.DEFAULT,
        memory_eligibility=MemoryEligibility.INELIGIBLE,
    )
    store.append_event(
        EventEnvelope(
            event_id="evt-1",
            kind=EventKind.NOTE,
            occurred_at="2026-07-21T00:00:00Z",
            source="cc",
            provenance=Provenance.MACHINE_OBSERVATION,
            policy_version="v0",
            payload={"note": "hello"},
        )
    )

    first = store.replay()
    second = store.replay()
    assert first == second


def test_replay_from_seq_only_folds_tail(store: ModelOsStore) -> None:
    """replay(from_seq) folds only events after that seq."""

    s0 = store.append_event(
        EventEnvelope(
            event_id="evt-0",
            kind=EventKind.NOTE,
            occurred_at="2026-07-21T00:00:00Z",
            source="t",
            provenance=Provenance.MACHINE_OBSERVATION,
            policy_version="v0",
            payload={"i": 0},
        )
    )
    store.append_event(
        EventEnvelope(
            event_id="evt-1",
            kind=EventKind.NOTE,
            occurred_at="2026-07-21T00:00:01Z",
            source="t",
            provenance=Provenance.MACHINE_OBSERVATION,
            policy_version="v0",
            payload={"i": 1},
        )
    )
    tail = store.replay(from_seq=s0)
    assert tail.last_seq == s0 + 1


# ----------------------------------------- unknown_requires_reconcile (7) ---


def test_unconfirmed_side_effect_replays_as_unknown(store: ModelOsStore) -> None:
    """An action that may have happened but whose result wasn't written back
    must surface as an unknown reconcile entry, never auto-replayed."""

    store.append_event(
        EventEnvelope(
            event_id="evt-side",
            kind=EventKind.SIDE_EFFECT_UNCONFIRMED,
            occurred_at="2026-07-21T00:00:00Z",
            source="kernel",
            provenance=Provenance.UNKNOWN,
            policy_version="v0",
            payload={
                "description": "ran git commit but process died before ack",
                "command_hash": "sha256:abc",
            },
            outcome="requires_reconcile",
        )
    )

    snap = store.read_snapshot()
    assert len(snap.unknown_actions) == 1
    action = snap.unknown_actions[0]
    assert action.reconcile_kind == "requires_reconcile"
    assert action.event_id == "evt-side"


def test_pending_channel_lost_replays_as_unknown(store: ModelOsStore) -> None:
    """Per spike-083: a lost pending control channel becomes
    unknown_requires_user_restart, never auto-replayed."""

    store.append_event(
        EventEnvelope(
            event_id="evt-pending",
            kind=EventKind.PENDING_CHANNEL_LOST,
            occurred_at="2026-07-21T00:00:00Z",
            source="kernel",
            provenance=Provenance.UNKNOWN,
            policy_version="v0",
            payload={"description": "approval channel lost on restart"},
            outcome="requires_user_restart",
        )
    )
    snap = store.read_snapshot()
    assert len(snap.unknown_actions) == 1
    assert snap.unknown_actions[0].reconcile_kind == "requires_user_restart"


def test_replay_does_not_mutate_journal(store: ModelOsStore) -> None:
    """Replay is purely additive — reading the snapshot writes nothing new."""

    store.append_event(
        EventEnvelope(
            event_id="evt-x",
            kind=EventKind.NOTE,
            occurred_at="2026-07-21T00:00:00Z",
            source="t",
            provenance=Provenance.MACHINE_OBSERVATION,
            policy_version="v0",
            payload={"i": 1},
        )
    )
    before = store.list_events()
    store.replay()
    store.read_snapshot()
    after = store.list_events()
    assert before == after


# ----------------------------------------- decisions redacted (C1, criterion 6) ---


def test_store_redacts_decision_signals_before_persisting(
    store: ModelOsStore,
) -> None:
    """Decisions must not bypass redaction: secrets under structured fields
    (signals/budget) never land in SQLite; a token-shaped ``reason`` is
    scrubbed; structural fields (choice/candidates) survive.

    Note: ``redact_payload`` scrubs by KEY NAME and whole-value token shape,
    not by content-scanning free text. A token embedded mid-string inside a
    free-text reason is the caller's responsibility to sanitise — the store
    guarantees structured fields and token-shaped scalars are clean.
    """

    decision = DecisionRecord(
        decision_id="dec-secret",
        kind="route",
        decided_at="2026-07-21T00:00:00Z",
        signals={
            "api_key": "sk-LEAK-1234567890abcdef",
            "prompt": "user said: my password is hunter2",
        },
        candidates=["fast", "deep"],
        choice="deep",
        reason="decided because the validator failed twice",
        policy_version="v0",
        budget_before={"https_proxy": "http://127.0.0.1:7897"},
    )
    store.append_decision(decision)

    decisions = store.list_decisions()
    assert len(decisions) == 1
    stored = decisions[0][1]
    assert "sk-LEAK" not in str(stored.signals)
    assert "hunter2" not in str(stored.signals)
    assert "127.0.0.1:7897" not in str(stored.budget_before)
    # structural fields survive
    assert stored.choice == "deep"
    assert stored.candidates == ["fast", "deep"]
    assert stored.reason == "decided because the validator failed twice"

    # a token-shaped whole-value reason IS scrubbed
    token_decision = DecisionRecord(
        decision_id="dec-token-reason",
        kind="route",
        decided_at="2026-07-21T00:00:00Z",
        signals={},
        candidates=["fast"],
        choice="fast",
        reason="sk-LEAK-1234567890abcdef",
        policy_version="v0",
    )
    store.append_decision(token_decision)
    stored_token = store.list_decisions()[1][1]
    assert stored_token.reason != "sk-LEAK-1234567890abcdef"
    assert "sk-LEAK" not in str(stored_token.reason)


# ---------------------------------- atomic pair idempotency (C2, criterion 3) ---


def test_append_decision_with_intent_is_idempotent(store: ModelOsStore) -> None:
    """Retrying the exact (decision, intent_event) pair returns the original
    seqs and writes no duplicate rows."""

    decision = DecisionRecord(
        decision_id="dec-idem",
        kind="route",
        decided_at="2026-07-21T00:00:00Z",
        signals={"u": 0.8},
        candidates=["fast"],
        choice="fast",
        reason="ok",
        policy_version="v0",
    )
    intent = EventEnvelope(
        event_id="evt-idem",
        kind=EventKind.NOTE,
        occurred_at="2026-07-21T00:00:00Z",
        source="kernel",
        provenance=Provenance.MACHINE_OBSERVATION,
        policy_version="v0",
        payload={"k": 1},
    )

    first_d, first_e = store.append_decision_with_intent(decision, intent)
    second_d, second_e = store.append_decision_with_intent(decision, intent)
    assert (first_d, first_e) == (second_d, second_e)
    assert len(store.list_decisions()) == 1
    assert len(store.list_events()) == 1


def test_append_decision_with_intent_partial_pair_raises(
    store: ModelOsStore,
) -> None:
    """If only one of (decision_id, event_id) already exists, the pair was
    split — surface it rather than silently accepting the orphan."""

    # pre-seed only the event
    store.append_event(
        EventEnvelope(
            event_id="evt-orphan",
            kind=EventKind.NOTE,
            occurred_at="2026-07-21T00:00:00Z",
            source="t",
            provenance=Provenance.MACHINE_OBSERVATION,
            policy_version="v0",
            payload={"i": 1},
        )
    )
    decision = DecisionRecord(
        decision_id="dec-fresh",
        kind="route",
        decided_at="2026-07-21T00:00:00Z",
        signals={"u": 0.5},
        candidates=["fast"],
        choice="fast",
        reason="ok",
        policy_version="v0",
    )
    intent = EventEnvelope(
        event_id="evt-orphan",  # already exists, but decision is new
        kind=EventKind.NOTE,
        occurred_at="2026-07-21T00:00:00Z",
        source="t",
        provenance=Provenance.MACHINE_OBSERVATION,
        policy_version="v0",
        payload={"i": 2},
    )
    with pytest.raises(sqlite3.IntegrityError):
        store.append_decision_with_intent(decision, intent)
    # the fresh decision was rolled back, not orphaned
    assert len(store.list_decisions()) == 0


# ----------------------------------------- mixed policy replay (criterion 1) ---


def test_replay_handles_mixed_policy_versions(store: ModelOsStore) -> None:
    """Spec criterion 1 + "schema 有版本": events recorded under different
    policy versions replay into the same deterministic snapshot, and both
    versions are queryable from the journal."""

    store.create_work_item(
        kind=WorkItemKind.TASK,
        owner_ref="user",
        task_id="task-A",
        session_purpose=SessionPurpose.FOREGROUND,
        memory_eligibility=MemoryEligibility.ELIGIBLE,
    )
    wi_snapshot = store.read_snapshot()
    wi_id = wi_snapshot.work_items[0].work_item_id

    store.append_event(
        EventEnvelope(
            event_id="evt-v0",
            kind=EventKind.NOTE,
            occurred_at="2026-07-21T00:00:00Z",
            source="cc",
            provenance=Provenance.MACHINE_OBSERVATION,
            policy_version="v0",
            payload={"policy": "old"},
            work_item_id=wi_id,
        )
    )
    store.append_event(
        EventEnvelope(
            event_id="evt-v1",
            kind=EventKind.NOTE,
            occurred_at="2026-07-21T00:00:01Z",
            source="cc",
            provenance=Provenance.MACHINE_OBSERVATION,
            policy_version="v1",
            payload={"policy": "new"},
            work_item_id=wi_id,
        )
    )

    first = store.replay()
    second = store.replay()
    assert first == second
    # both policy versions are retained in the journal
    versions = {ev.policy_version for _, ev in store.list_events()}
    assert versions >= {"v0", "v1"}
