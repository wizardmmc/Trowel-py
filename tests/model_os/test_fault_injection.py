"""决策与 intent 原子写入的故障注入测试。

通过 SQLite UNIQUE 约束制造真实的事务中断，验证任一语句失败都会回滚整个原子对，
不会留下半条记录、重复 seq 或提前消耗序号。
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
    store.append_event(_note("evt-crash"))  # 在独立事务预占 event_id
    baseline_events = len(store.list_events())
    baseline_decisions = len(store.list_decisions())

    with pytest.raises(sqlite3.IntegrityError):
        store.append_decision_with_intent(
            _decision("dec-crash"),
            _note("evt-crash"),  # 重复 event_id 触发 IntegrityError
        )

    assert len(store.list_events()) == baseline_events
    assert len(store.list_decisions()) == baseline_decisions


def test_crash_does_not_advance_seq(store: ModelOsStore) -> None:
    store.append_event(_note("evt-crash"))  # 事件 seq 为 1
    with pytest.raises(sqlite3.IntegrityError):
        store.append_decision_with_intent(
            _decision("dec-crash"),
            _note("evt-crash"),  # 重复事件触发回滚，决策不会落盘。
        )

    # 回滚没有消耗决策序号；下一条决策仍为 1，下一条事件则为 2。
    d_seq, e_seq = store.append_decision_with_intent(
        _decision("dec-ok"), _note("evt-ok")
    )
    assert d_seq == 1
    assert e_seq == 2


def test_successful_atomic_pair_commits_both(store: ModelOsStore) -> None:
    d_seq, e_seq = store.append_decision_with_intent(
        _decision("dec-ok"), _note("evt-ok")
    )
    assert d_seq >= 1
    assert e_seq >= 1
    assert len(store.list_decisions()) == 1
    assert len(store.list_events()) == 1
