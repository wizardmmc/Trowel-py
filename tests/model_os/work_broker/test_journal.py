from __future__ import annotations

from pathlib import Path

import pytest

from trowel_py.model_os.store import ModelOsStore
from trowel_py.model_os.work_broker import (
    BudgetDimensions,
    ModelTier,
    UsageRecord,
    WorkBroker,
    WorkDenial,
    WorkKind,
    WorkLease,
    WorkRequest,
)
from trowel_py.quota.types import Provider
from tests.model_os.work_broker._support import FakeClock, _default, _policy


def test_grant_and_denial_are_explainable_without_storing_account_id(
    db_path: Path,
    clock: FakeClock,
) -> None:
    broker = WorkBroker(db_path, policy=_policy(), now_fn=clock)
    broker.open()
    granted = broker.request(_default(account_id="private-account"))
    denied = broker.request(_default(account_id="private-account"))
    assert isinstance(granted, WorkLease)
    assert isinstance(denied, WorkDenial)
    assert granted.decision_id is not None
    assert denied.decision_id is not None
    assert broker._conn is not None
    rows = broker._conn.execute(
        "SELECT decision_id, cause_id, candidates, choice, reason "
        "FROM decisions ORDER BY seq"
    ).fetchall()
    broker.close()

    assert [(row["choice"], row["reason"]) for row in rows] == [
        ("grant", "granted"),
        ("defer", "slot_busy"),
    ]
    assert all("private-account" not in row["candidates"] for row in rows)
    assert rows[0]["cause_id"] is None
    assert granted.lease_id in rows[0]["candidates"]

    store = ModelOsStore(db_path)
    store.open()
    try:
        explanation = store.explain_decision(granted.decision_id)
        assert explanation.choice == "grant"
        assert explanation.command.status == "not_applicable"
    finally:
        store.close()


def test_idempotent_grant_reuses_one_arbitration_decision(
    db_path: Path,
    clock: FakeClock,
) -> None:
    broker = WorkBroker(db_path, policy=_policy(), now_fn=clock)
    broker.open()
    first = broker.request(_default(account_id="glm-a", idem="same"))
    assert isinstance(first, WorkLease)
    broker.close()

    reopened = WorkBroker(db_path, policy=_policy(), now_fn=clock)
    reopened.open()
    try:
        second = reopened.request(_default(account_id="glm-a", idem="same"))
        assert isinstance(second, WorkLease)
        assert second.decision_id == first.decision_id
        assert reopened._conn is not None
        count = reopened._conn.execute(
            "SELECT COUNT(*) FROM decisions WHERE kind='work_broker.arbitrate'"
        ).fetchone()[0]
        assert count == 1
    finally:
        reopened.close()


def test_new_usage_observation_records_budget_before_and_after(
    db_path: Path,
    clock: FakeClock,
) -> None:
    broker = WorkBroker(db_path, policy=_policy(), now_fn=clock)
    broker.open()
    try:
        lease = broker.request(_default(account_id="glm-a"))
        assert isinstance(lease, WorkLease)
        usage = UsageRecord(
            calls=1,
            input_tokens=2,
            output_tokens=3,
            cost=None,
            occurred_at=clock.iso(),
            observation_id="usage-1",
        )
        broker.record_usage(lease.lease_id, lease.fencing_token, usage)
        broker.record_usage(lease.lease_id, lease.fencing_token, usage)
        assert broker._conn is not None
        rows = broker._conn.execute(
            "SELECT cause_id, candidates, budget_before, budget_after "
            "FROM decisions WHERE kind='work_broker.usage'"
        ).fetchall()
        assert len(rows) == 1
        assert rows[0]["cause_id"] == lease.decision_id
        assert '"calls":0' in rows[0]["budget_before"]
        assert '"calls":1' in rows[0]["budget_after"]
        assert '"cost_source":"unknown"' in rows[0]["candidates"]
    finally:
        broker.close()


def test_private_free_text_cannot_enter_public_journal_identifiers() -> None:
    with pytest.raises(ValueError, match="task_id must be a structured reference"):
        _default(account_id="glm-a", task_id="private raw prompt")


def test_durable_incubation_settlement_records_usage_decision(
    db_path: Path,
    clock: FakeClock,
) -> None:
    broker = WorkBroker(db_path, policy=_policy(), now_fn=clock)
    broker.open()
    try:
        lease = broker.request(
            WorkRequest(
                kind=WorkKind.INCUBATION,
                provider=Provider.GLM,
                account_id="glm-a",
                model_tier=ModelTier.DEEP,
                task_id="task-incubation",
                work_item_id="work-incubation",
                budget_cap=BudgetDimensions(calls=2),
            )
        )
        assert isinstance(lease, WorkLease)
        usage = UsageRecord(
            calls=1,
            occurred_at=clock.iso(),
            observation_id="settlement-1",
        )
        assert broker.settle_incubation_usage("work-incubation", usage)
        assert broker.settle_incubation_usage("work-incubation", usage)
        assert broker._conn is not None
        rows = broker._conn.execute(
            "SELECT cause_id FROM decisions WHERE kind='work_broker.usage'"
        ).fetchall()
        assert len(rows) == 1
        assert rows[0]["cause_id"] == lease.decision_id
    finally:
        broker.close()
