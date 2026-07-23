from __future__ import annotations

from pathlib import Path

import pytest

from trowel_py.model_os.work_broker import (
    BudgetDimensions,
    DenialReason,
    UsageRecord,
    WorkBroker,
    WorkKind,
    WorkLease,
)
from trowel_py.quota.types import (
    Provider,
)
from tests.model_os.work_broker._support import (
    FakeClock,
    _default,
    _fg,
    _policy,
    _use,
)


def test_usage_dimensions_from_lease(broker: WorkBroker, clock: FakeClock) -> None:

    fg = broker.request(_fg(account_id="glm-a", task_id="t-real"))
    assert isinstance(fg, WorkLease)
    _use(broker, fg, calls=3, clock=clock)

    assert broker.usage_totals(provider=Provider.GLM).calls == 3
    assert broker.usage_totals(task_id="t-real").calls == 3
    assert broker.usage_totals(work_kind=WorkKind.FOREGROUND).calls == 3

    assert broker.usage_totals(provider=Provider.CODEX).calls == 0
    assert broker.usage_totals(work_kind=WorkKind.DEFAULT).calls == 0
    assert broker.usage_totals(task_id="t-forged").calls == 0


def test_usage_attribution_and_observation_idempotency(
    broker: WorkBroker, clock: FakeClock
) -> None:

    d = broker.request(_default(account_id="glm-a", task_id="task-1"))
    assert isinstance(d, WorkLease)
    _use(broker, d, calls=3, cost=0.5, observation_id="obs-1", clock=clock)
    _use(broker, d, calls=3, cost=0.5, observation_id="obs-1", clock=clock)
    assert broker.usage_totals(work_kind=WorkKind.DEFAULT).calls == 3
    assert broker.usage_totals(account_id="glm-a").cost == pytest.approx(0.5)


def test_usage_rejects_negative_and_garbage(
    broker: WorkBroker, clock: FakeClock
) -> None:
    d = broker.request(_default(account_id="glm-a", task_id="t"))
    assert isinstance(d, WorkLease)
    with pytest.raises(ValueError):
        broker.record_usage(
            d.lease_id,
            d.fencing_token,
            UsageRecord(calls=-1, occurred_at=clock.iso()),
        )
    with pytest.raises(ValueError):
        broker.record_usage(
            d.lease_id,
            d.fencing_token,
            UsageRecord(calls=1, occurred_at="garbage"),
        )
    with pytest.raises(ValueError):
        broker.record_usage(
            d.lease_id,
            d.fencing_token,
            UsageRecord(calls=1, occurred_at="2026-07-23T00:00:00"),
        )


def test_budget_cap_only_narrows(broker: WorkBroker) -> None:

    narrow = broker.request(
        _default(account_id="glm-a", budget_cap=BudgetDimensions(calls=50))
    )
    assert isinstance(narrow, WorkLease)
    assert narrow.granted_cap == BudgetDimensions(calls=50)
    broker.release(narrow.lease_id, narrow.fencing_token)

    widen = broker.request(
        _default(account_id="glm-b", budget_cap=BudgetDimensions(calls=200))
    )
    assert isinstance(widen, WorkLease)
    assert widen.granted_cap == BudgetDimensions(calls=100)
    broker.release(widen.lease_id, widen.fencing_token)

    empty = broker.request(_default(account_id="glm-a", budget_cap=BudgetDimensions()))
    assert isinstance(empty, WorkLease)
    assert empty.granted_cap == BudgetDimensions(calls=100)


def test_budget_gate_uses_narrowed_cap(broker: WorkBroker, clock: FakeClock) -> None:

    d = broker.request(
        _default(account_id="glm-a", task_id="t", budget_cap=BudgetDimensions(calls=2))
    )
    assert isinstance(d, WorkLease)
    _use(broker, d, calls=2, clock=clock)
    broker.release(d.lease_id, d.fencing_token)
    denial = broker.request(
        _default(account_id="glm-a", budget_cap=BudgetDimensions(calls=2))
    )
    assert isinstance(denial, WorkLease) is False
    assert denial.reason is DenialReason.BUDGET_EXHAUSTED

    other = broker.request(_default(account_id="glm-a", task_id="t2"))
    assert isinstance(other, WorkLease)


def test_unknown_cost_stays_unknown(broker: WorkBroker, clock: FakeClock) -> None:

    # 未知费用不能折成 0，也不能据此误判额度耗尽。
    d = broker.request(_default(account_id="glm-a", task_id="t"))
    assert isinstance(d, WorkLease)
    _use(broker, d, calls=1, cost=None, clock=clock)
    totals = broker.usage_totals(work_kind=WorkKind.DEFAULT)
    assert totals.cost is None


def test_known_and_unknown_cost_yields_unknown(
    broker: WorkBroker, clock: FakeClock
) -> None:
    d = broker.request(_default(account_id="glm-a", task_id="t"))
    assert isinstance(d, WorkLease)
    _use(broker, d, calls=1, cost=0.3, observation_id="a", clock=clock)
    _use(broker, d, calls=1, cost=None, observation_id="b", clock=clock)
    assert broker.usage_totals(work_kind=WorkKind.DEFAULT).cost is None


def test_cost_cap_not_falsely_tripped_by_unknown(
    db_path: Path, clock: FakeClock
) -> None:

    broker = WorkBroker(
        db_path,
        policy=_policy(default_cap=BudgetDimensions(cost=0.5)),
        read_model=None,
        now_fn=clock,
    )
    broker.open()
    try:
        d = broker.request(_default(account_id="glm-a", task_id="t"))
        assert isinstance(d, WorkLease)
        _use(broker, d, cost=None, clock=clock)
        broker.release(d.lease_id, d.fencing_token)

        nxt = broker.request(_default(account_id="glm-a", task_id="t2"))
        assert isinstance(nxt, WorkLease)
    finally:
        broker.close()


def test_observation_id_scoped_per_lease(broker: WorkBroker, clock: FakeClock) -> None:

    a = broker.request(_default(account_id="glm-a", task_id="ta"))
    b = broker.request(_default(account_id="glm-b", task_id="tb"))
    assert isinstance(a, WorkLease) and isinstance(b, WorkLease)
    _use(broker, a, calls=3, observation_id="obs-1", clock=clock)
    _use(broker, b, calls=7, observation_id="obs-1", clock=clock)
    assert broker.usage_totals(task_id="ta").calls == 3
    assert broker.usage_totals(task_id="tb").calls == 7


def test_usage_retry_returns_consistent_day(
    broker: WorkBroker, clock: FakeClock
) -> None:

    d = broker.request(_default(account_id="glm-a", task_id="t"))
    assert isinstance(d, WorkLease)

    # 带时区偏移的观测仍按 UTC 日归属累计。
    offset_ts = "2026-07-23T00:30:00+08:00"
    broker.record_usage(
        d.lease_id,
        d.fencing_token,
        UsageRecord(calls=3, occurred_at=offset_ts, observation_id="obs-x"),
    )
    again = broker.record_usage(
        d.lease_id,
        d.fencing_token,
        UsageRecord(calls=3, occurred_at=offset_ts, observation_id="obs-x"),
    )

    assert again.calls == 3
    assert broker.usage_totals(day="2026-07-22").calls == 3
    assert broker.usage_totals(day="2026-07-23").calls == 0


def test_granted_cap_reflected_on_lease(broker: WorkBroker) -> None:
    fg = broker.request(_fg(account_id="glm-a"))
    assert isinstance(fg, WorkLease)
    assert fg.granted_cap is None
    d = broker.request(_default(account_id="glm-b"))
    assert isinstance(d, WorkLease)
    assert d.granted_cap == broker.policy.default_cap
