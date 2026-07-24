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


def test_store_redacts_decision_signals_before_persisting(
    store: ModelOsStore,
) -> None:
    # 结构化字段按键名脱敏；自由文本只识别整个值形似 token 的情况。
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
    assert stored.choice == "deep"
    assert stored.candidates == ["fast", "deep"]
    assert stored.reason == "decided because the validator failed twice"

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


def test_append_decision_with_intent_is_idempotent(store: ModelOsStore) -> None:
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
        decided_at="2026-07-21T00:00:00Z",
        signals={"u": 0.5},
        candidates=["fast"],
        choice="fast",
        reason="ok",
        policy_version="v0",
    )
    intent = EventEnvelope(
        event_id="evt-orphan",
        kind=EventKind.NOTE,
        occurred_at="2026-07-21T00:00:00Z",
        source="t",
        provenance=Provenance.MACHINE_OBSERVATION,
        policy_version="v0",
        payload={"i": 2},
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
