from __future__ import annotations

from trowel_py.model_os.store import ModelOsStore
from trowel_py.model_os.types import (
    DecisionRecord,
    EventEnvelope,
    EventKind,
    MemoryEligibility,
    Provenance,
    SessionPurpose,
    WorkItemKind,
)


def test_append_event_assigns_monotonic_seq(store: ModelOsStore) -> None:
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


def test_replay_twice_yields_equal_snapshot(store: ModelOsStore) -> None:
    store.create_task_from_user_request(
        original_goal="t", idempotency_key="k", authorization_scope="d"
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
