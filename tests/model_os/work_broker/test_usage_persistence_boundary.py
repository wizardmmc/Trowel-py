from __future__ import annotations

import threading
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import pytest

from trowel_py.model_os import work_broker
from trowel_py.model_os.work_broker import (
    ModelTier,
    UsageRecord,
    UsageTotals,
    WorkBroker,
    WorkKind,
    WorkLease,
)
from trowel_py.quota.types import Provider
from tests.model_os.work_broker._support import FakeClock, _default, _policy


class InjectedTotalsFailure(Exception):
    pass


def test_totals_facade_injects_current_connection_and_type(monkeypatch) -> None:
    connection = sqlite3.connect(":memory:")
    totals_type = object()
    result = object()
    captured: dict[str, object] = {}

    def run(candidate, **kwargs):
        captured.update(connection=candidate, kwargs=kwargs)
        return result

    broker = WorkBroker.__new__(WorkBroker)
    broker._conn = connection
    monkeypatch.setattr(work_broker, "_run_usage_totals_in_tx", run)
    monkeypatch.setattr(work_broker, "UsageTotals", totals_type)

    try:
        assert (
            broker._totals_in_tx(
                day="2026-07-24",
                work_kind=WorkKind.DEFAULT,
                provider=Provider.GLM,
                account_id="glm-a",
                task_id="task-1",
                model_tier=ModelTier.FAST,
            )
            is result
        )
        assert captured == {
            "connection": connection,
            "kwargs": {
                "day": "2026-07-24",
                "work_kind": WorkKind.DEFAULT,
                "provider": Provider.GLM,
                "account_id": "glm-a",
                "task_id": "task-1",
                "model_tier": ModelTier.FAST,
                "totals_factory": totals_type,
            },
        }
    finally:
        connection.close()


def test_record_usage_persistence_runs_inside_broker_transaction(
    broker: WorkBroker,
    clock: FakeClock,
    monkeypatch,
) -> None:
    lease = broker.request(_default(account_id="glm-a"))
    assert isinstance(lease, WorkLease)
    calls: list[tuple[str, dict[str, Any]]] = []
    expected = UsageTotals(1, 2, 3, 0.5, 4)

    def assert_transaction(connection) -> None:
        assert broker._conn is not None and connection is broker._conn
        assert connection.in_transaction

    def seen(connection, **kwargs):
        assert_transaction(connection)
        calls.append(("seen", kwargs))
        return False

    def mark_started(connection, **kwargs):
        assert_transaction(connection)
        calls.append(("started", kwargs))

    def insert(connection, **kwargs):
        assert_transaction(connection)
        calls.append(("insert", kwargs))

    def totals(**kwargs):
        assert broker._conn is not None and broker._conn.in_transaction
        calls.append(("totals", kwargs))
        return expected

    monkeypatch.setattr(work_broker, "_usage_observation_seen_in_tx", seen)
    monkeypatch.setattr(work_broker, "_mark_usage_lease_started_in_tx", mark_started)
    monkeypatch.setattr(work_broker, "_insert_usage_in_tx", insert)
    monkeypatch.setattr(broker, "_totals_in_tx", totals)
    usage = UsageRecord(
        calls=1,
        input_tokens=2,
        output_tokens=3,
        cost=0.5,
        wall_seconds=4,
        occurred_at=clock.iso(),
        observation_id="observation-1",
    )

    assert broker.record_usage(lease.lease_id, lease.fencing_token, usage) is expected
    assert [name for name, _ in calls] == ["seen", "started", "insert", "totals"]
    assert broker._conn is not None
    assert not broker._conn.in_transaction
    assert calls[0][1] == {
        "lease_id": lease.lease_id,
        "observation_id": "observation-1",
    }
    assert calls[1][1] == {"lease_id": lease.lease_id}
    assert calls[2][1]["lease_id"] == lease.lease_id
    assert calls[2][1]["usage"] is usage
    assert calls[3][1] == {
        "work_kind": WorkKind.DEFAULT,
        "provider": Provider.GLM,
        "account_id": "glm-a",
        "day": clock().date().isoformat(),
    }


def test_record_usage_rolls_back_started_and_insert_when_totals_fails(
    broker: WorkBroker,
    clock: FakeClock,
    monkeypatch,
) -> None:
    lease = broker.request(_default(account_id="glm-a"))
    assert isinstance(lease, WorkLease)

    def fail_totals(**kwargs):
        raise InjectedTotalsFailure

    monkeypatch.setattr(broker, "_totals_in_tx", fail_totals)
    with pytest.raises(InjectedTotalsFailure):
        broker.record_usage(
            lease.lease_id,
            lease.fencing_token,
            UsageRecord(
                calls=1,
                occurred_at=clock.iso(),
                observation_id="rollback-observation",
            ),
        )

    assert broker._conn is not None
    lease_row = broker._conn.execute(
        "SELECT started FROM work_leases WHERE lease_id=?",
        (lease.lease_id,),
    ).fetchone()
    usage_count = broker._conn.execute(
        "SELECT COUNT(*) AS count FROM work_usage WHERE lease_id=?",
        (lease.lease_id,),
    ).fetchone()
    assert lease_row["started"] == 0
    assert usage_count["count"] == 0
    assert not broker._conn.in_transaction


def test_two_brokers_serialize_duplicate_observation(
    db_path: Path,
    clock: FakeClock,
) -> None:
    first = WorkBroker(db_path, policy=_policy(), now_fn=clock)
    second = WorkBroker(db_path, policy=_policy(), now_fn=clock)
    first.open()
    second.open()
    try:
        lease = first.request(_default(account_id="glm-a"))
        assert isinstance(lease, WorkLease)
        barrier = threading.Barrier(2)

        def record(broker: WorkBroker):
            barrier.wait()
            return broker.record_usage(
                lease.lease_id,
                lease.fencing_token,
                UsageRecord(
                    calls=3,
                    occurred_at=clock.iso(),
                    observation_id="shared-observation",
                ),
            )

        with ThreadPoolExecutor(max_workers=2) as pool:
            totals = tuple(pool.map(record, (first, second)))

        assert tuple(total.calls for total in totals) == (3, 3)
        assert first._conn is not None
        row = first._conn.execute(
            "SELECT COUNT(*) AS count, SUM(calls) AS calls FROM work_usage "
            "WHERE lease_id=? AND observation_id=?",
            (lease.lease_id, "shared-observation"),
        ).fetchone()
        assert (row["count"], row["calls"]) == (1, 3)
    finally:
        second.close()
        first.close()
