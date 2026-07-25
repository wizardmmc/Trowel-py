from __future__ import annotations

import sqlite3

import pytest

from trowel_py.model_os.store import ModelOsStore
from trowel_py.model_os.types import (
    DecisionRecord,
    DecisionDisposition,
    EventEnvelope,
    EventKind,
    Provenance,
)


def test_store_redacts_decision_signals_before_persisting(
    store: ModelOsStore,
) -> None:
    # Decision v2 只持久化结构引用；任意正文在进入 SQLite 前被拒绝。
    decision = DecisionRecord(
        decision_id="dec-secret",
        kind="route",
        disposition=DecisionDisposition.NO_ACTION,
        decided_at="2026-07-21T00:00:00Z",
        signals={"refs": ["event.usage.high"]},
        candidates=["fast", "deep"],
        choice="deep",
        reason="validator_failed_twice",
        policy_version="v0",
        budget_before={"calls": 2},
    )
    store.append_decision(decision)

    decisions = store.list_decisions()
    assert len(decisions) == 1
    stored = decisions[0][1]
    assert stored.signals == {"refs": ["event.usage.high"]}
    assert stored.budget_before == {"calls": 2}
    assert stored.choice == "deep"
    assert stored.candidates == ["fast", "deep"]
    assert stored.reason == "validator_failed_twice"

    token_decision = DecisionRecord(
        decision_id="dec-token-reason",
        kind="route",
        disposition=DecisionDisposition.NO_ACTION,
        decided_at="2026-07-21T00:00:00Z",
        signals={"refs": []},
        candidates=["fast"],
        choice="fast",
        reason="sk-leak-1234567890abcdef",
        policy_version="v0",
    )
    with pytest.raises(ValueError):
        store.append_decision(token_decision)


def test_append_decision_with_intent_is_idempotent(store: ModelOsStore) -> None:
    decision = DecisionRecord(
        decision_id="dec-idem",
        kind="route",
        disposition=DecisionDisposition.EXECUTE,
        decided_at="2026-07-21T00:00:00Z",
        signals={"refs": ["event.usage.high"]},
        candidates=["fast"],
        choice="fast",
        reason="usage_high",
        policy_version="v0",
        correlation_id="command.idem",
    )
    intent = EventEnvelope(
        event_id="evt-idem",
        kind=EventKind.COMMAND_INTENT,
        occurred_at="2026-07-21T00:00:00Z",
        source="kernel",
        provenance=Provenance.MACHINE_OBSERVATION,
        policy_version="v0",
        payload={
            "command_kind": "route.select",
            "target_ref": "episode.1",
            "idempotency_key_hash": "sha256:idem123",
            "args_hash": "sha256:args123",
        },
        cause_id="dec-idem",
        correlation_id="command.idem",
    )

    first_d, first_e = store.append_decision_with_intent(decision, intent)
    second_d, second_e = store.append_decision_with_intent(decision, intent)
    assert (first_d, first_e) == (second_d, second_e)
    assert len(store.list_decisions()) == 1
    assert len(store.list_events()) == 1


def test_append_decision_with_intent_partial_pair_raises(
    store: ModelOsStore,
) -> None:
    # decision/event 只存在一侧表示原子对已分裂，必须报错并回滚新记录。
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
        disposition=DecisionDisposition.EXECUTE,
        decided_at="2026-07-21T00:00:00Z",
        signals={"refs": ["event.usage.mid"]},
        candidates=["fast"],
        choice="fast",
        reason="usage_mid",
        policy_version="v0",
        correlation_id="command.fresh",
    )
    intent = EventEnvelope(
        event_id="evt-orphan",
        kind=EventKind.COMMAND_INTENT,
        occurred_at="2026-07-21T00:00:00Z",
        source="t",
        provenance=Provenance.MACHINE_OBSERVATION,
        policy_version="v0",
        payload={
            "command_kind": "route.select",
            "target_ref": "episode.1",
            "idempotency_key_hash": "sha256:idem456",
            "args_hash": "sha256:args456",
        },
        cause_id="dec-fresh",
        correlation_id="command.fresh",
    )
    with pytest.raises(sqlite3.IntegrityError):
        store.append_decision_with_intent(decision, intent)
    assert len(store.list_decisions()) == 0


def test_replay_handles_mixed_policy_versions(store: ModelOsStore) -> None:
    store.create_task_from_user_request(
        original_goal="t", idempotency_key="k", authorization_scope="d"
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
    versions = {ev.policy_version for _, ev in store.list_events()}
    assert versions >= {"v0", "v1"}
