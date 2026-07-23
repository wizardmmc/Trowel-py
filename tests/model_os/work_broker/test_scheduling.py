from __future__ import annotations

import datetime as _dt
from pathlib import Path


from trowel_py.model_os.work_broker import (
    CatchupPolicy,
    DenialReason,
    WorkBroker,
    WorkKind,
    WorkLease,
    WorkRequest,
)
from trowel_py.quota.types import (
    Provider,
)
from tests.model_os.work_broker._support import (
    FakeClock,
    _BASE,
    _maint,
    _policy,
)


def test_maintenance_catchup_live_then_merge(broker: WorkBroker) -> None:

    first = broker.request(_maint())
    assert isinstance(first, WorkLease)
    second = broker.request(_maint())
    assert isinstance(second, WorkLease) is False
    assert second.reason is DenialReason.CATCHUP_ALREADY_DONE


def test_maintenance_catchup_completes_on_complete(broker: WorkBroker) -> None:

    first = broker.request(_maint(period="2026-07-22"))
    assert isinstance(first, WorkLease)
    assert broker.complete(first.lease_id, first.fencing_token) is True
    again = broker.request(_maint(period="2026-07-22"))
    assert isinstance(again, WorkLease) is False
    assert again.reason is DenialReason.CATCHUP_ALREADY_DONE


def test_maintenance_abandon_does_not_complete(broker: WorkBroker) -> None:

    first = broker.request(_maint(period="2026-07-22"))
    assert isinstance(first, WorkLease)
    assert broker.release(first.lease_id, first.fencing_token) is True
    again = broker.request(_maint(period="2026-07-22"))
    assert isinstance(again, WorkLease)


def test_maintenance_different_periods_both_run(broker: WorkBroker) -> None:
    a = broker.request(_maint(period="2026-07-21"))
    b = broker.request(_maint(period="2026-07-22"))
    assert isinstance(a, WorkLease) and isinstance(b, WorkLease)


def test_default_missed_tick_is_dropped(db_path: Path, clock: FakeClock) -> None:
    broker = WorkBroker(db_path, policy=_policy(), read_model=None, now_fn=clock)
    broker.open()
    try:
        eight_h_ago = (_BASE - _dt.timedelta(hours=8)).isoformat()
        req = WorkRequest(
            kind=WorkKind.DEFAULT,
            provider=Provider.GLM,
            account_id="glm-a",
            catchup=CatchupPolicy.DEFAULT_DROP,
            scheduled_for=eight_h_ago,
        )
        denial = broker.request(req)
        assert isinstance(denial, WorkLease) is False
        assert denial.reason is DenialReason.STALE_TICK_DROPPED
    finally:
        broker.close()


def test_default_fresh_tick_runs(broker: WorkBroker) -> None:
    req = WorkRequest(
        kind=WorkKind.DEFAULT,
        provider=Provider.GLM,
        account_id="glm-a",
        catchup=CatchupPolicy.DEFAULT_DROP,
        scheduled_for=_BASE.isoformat(),
    )
    lease = broker.request(req)
    assert isinstance(lease, WorkLease)


def test_catchup_crash_self_heals(db_path: Path, clock: FakeClock) -> None:

    b1 = WorkBroker(db_path, policy=_policy(), read_model=None, now_fn=clock)
    b1.open()
    first = b1.request(_maint(period="2026-07-20"))
    assert isinstance(first, WorkLease)
    b1.close()

    clock.advance(b1.policy.lease_ttl_seconds + 5)
    b2 = WorkBroker(db_path, policy=_policy(), read_model=None, now_fn=clock)
    reclaimed = b2.open_recover()
    assert reclaimed >= 1

    redo = b2.request(_maint(period="2026-07-20"))
    assert isinstance(redo, WorkLease)
    b2.close()


def test_catchup_self_heal_is_durable_across_connections(
    db_path: Path, clock: FakeClock
) -> None:

    b1 = WorkBroker(db_path, policy=_policy(), read_model=None, now_fn=clock)
    b1.open()
    first = b1.request(_maint(period="2026-07-20"))
    assert isinstance(first, WorkLease)
    b1.close()

    clock.advance(b1.policy.lease_ttl_seconds + 5)
    b2 = WorkBroker(db_path, policy=_policy(), read_model=None, now_fn=clock)
    b2.open_recover()
    redo = b2.request(_maint(period="2026-07-20"))
    assert isinstance(redo, WorkLease)

    # 新连接必须看到自愈后的 lease，防止重授只存在于未提交事务。
    b3 = WorkBroker(db_path, policy=_policy(), read_model=None, now_fn=clock)
    b3.open()
    try:
        active_ids = {lse.lease_id for lse in b3.active_leases()}
        assert redo.lease_id in active_ids
    finally:
        b3.close()
        b2.close()
