from __future__ import annotations

from pathlib import Path

import pytest

from trowel_py.model_os.work_broker import (
    BudgetDimensions,
    DenialReason,
    IdempotencyConflict,
    ModelTier,
    StaleWorkLease,
    WorkBroker,
    WorkKind,
    WorkLease,
    WorkRequest,
)
from trowel_py.quota.types import Provider
from tests.model_os.work_broker._support import (
    FakeClock,
    _begin,
    _default,
    _fg,
    _maint,
    _policy,
    _use,
    _use_and_return,
)


def test_crash_recovery_reclaims_expired_lease(db_path: Path, clock: FakeClock) -> None:
    b1 = WorkBroker(
        db_path,
        policy=_policy(default_cap=BudgetDimensions()),
        read_model=None,
        now_fn=clock,
    )
    b1.open()
    dead = b1.request(_fg(account_id="glm-a"))
    assert isinstance(dead, WorkLease)
    b1.close()

    clock.advance(b1.policy.lease_ttl_seconds + 5)
    b2 = WorkBroker(
        db_path,
        policy=_policy(default_cap=BudgetDimensions()),
        read_model=None,
        now_fn=clock,
    )
    reclaimed = b2.open_recover()
    assert reclaimed >= 1
    fresh = b2.request(_fg(account_id="glm-a"))
    assert isinstance(fresh, WorkLease)
    assert fresh.fencing_token > dead.fencing_token
    b2.close()


def test_crash_recovery_old_fencing_token_dead(db_path: Path, clock: FakeClock) -> None:
    b1 = WorkBroker(
        db_path,
        policy=_policy(default_cap=BudgetDimensions()),
        read_model=None,
        now_fn=clock,
    )
    b1.open()
    dead = b1.request(_fg(account_id="glm-a", task_id="t-old"))
    assert isinstance(dead, WorkLease)
    b1.close()

    clock.advance(b1.policy.lease_ttl_seconds + 5)
    b2 = WorkBroker(
        db_path,
        policy=_policy(default_cap=BudgetDimensions()),
        read_model=None,
        now_fn=clock,
    )
    b2.open_recover()
    fresh = b2.request(_fg(account_id="glm-a", task_id="t-new"))
    assert isinstance(fresh, WorkLease)
    with pytest.raises(StaleWorkLease):
        _use(b2, dead, clock=clock)
    totals = _use_and_return(b2, fresh, clock=clock)
    assert totals.calls == 1
    b2.close()


def test_cleanup_recovery_is_idempotent_and_scoped_to_incubation(
    broker: WorkBroker, clock: FakeClock
) -> None:
    incubation = broker.request(
        WorkRequest(
            kind=WorkKind.INCUBATION,
            provider=Provider.GLM,
            account_id="glm-a",
            model_tier=ModelTier.DEEP,
            work_item_id="incubation-cleanup-work",
            budget_cap=BudgetDimensions(calls=1),
            idempotency_key="incubation-cleanup-lease",
        )
    )
    maintenance = broker.request(_maint(account_id="glm-b"))
    assert isinstance(incubation, WorkLease)
    assert isinstance(maintenance, WorkLease)
    clock.advance(broker.policy.lease_ttl_seconds + 1)
    cutoff = clock.iso()

    first = broker.recover_for_cleanup(
        "cleanup-command-1",
        before=cutoff,
        work_kind=WorkKind.INCUBATION,
    )
    replay = broker.recover_for_cleanup(
        "cleanup-command-1",
        before=cutoff,
        work_kind=WorkKind.INCUBATION,
    )

    assert first == replay == 1
    assert broker._conn is not None
    rows = {
        row["lease_id"]: row["released_at"]
        for row in broker._conn.execute(
            "SELECT lease_id, released_at FROM work_leases WHERE lease_id IN (?,?)",
            (incubation.lease_id, maintenance.lease_id),
        ).fetchall()
    }
    assert rows[incubation.lease_id] is not None
    assert rows[maintenance.lease_id] is None
    clock.advance(1)
    with pytest.raises(IdempotencyConflict):
        broker.recover_for_cleanup(
            "cleanup-command-1",
            before=clock.iso(),
            work_kind=WorkKind.INCUBATION,
        )


def test_started_incubation_is_not_reclaimed_only_because_lease_expired(
    broker: WorkBroker, clock: FakeClock
) -> None:
    incubation = broker.request(
        WorkRequest(
            kind=WorkKind.INCUBATION,
            provider=Provider.GLM,
            account_id="glm-a",
            model_tier=ModelTier.DEEP,
            work_item_id="incubation-unknown-work",
            budget_cap=BudgetDimensions(calls=1),
            idempotency_key="incubation-unknown-lease",
        )
    )
    assert isinstance(incubation, WorkLease)
    broker.begin_call(incubation.lease_id, incubation.fencing_token)
    clock.advance(broker.policy.lease_ttl_seconds + 1)

    assert broker.recover() == 0
    assert (
        broker.recover_for_cleanup(
            "cleanup-started-incubation",
            before=clock.iso(),
            work_kind=WorkKind.INCUBATION,
        )
        == 0
    )
    foreground = broker.request(_fg(account_id="glm-a"))
    assert not isinstance(foreground, WorkLease)
    assert broker._conn is not None
    row = broker._conn.execute(
        "SELECT released_at FROM work_leases WHERE lease_id=?",
        (incubation.lease_id,),
    ).fetchone()
    assert row["released_at"] is None


def test_expired_lease_rejects_record_usage(
    broker: WorkBroker, clock: FakeClock
) -> None:

    d = broker.request(_default(account_id="glm-a", task_id="t"))
    assert isinstance(d, WorkLease)
    clock.advance(broker.policy.lease_ttl_seconds + 1)
    with pytest.raises(StaleWorkLease):
        _use(broker, d, clock=clock)


def test_expired_lease_rejects_begin_call(broker: WorkBroker, clock: FakeClock) -> None:
    fg = broker.request(_fg(account_id="glm-a"))
    assert isinstance(fg, WorkLease)
    clock.advance(broker.policy.lease_ttl_seconds + 1)
    with pytest.raises(StaleWorkLease):
        broker.begin_call(fg.lease_id, fg.fencing_token)


def test_expired_lease_rejects_critical_section(
    broker: WorkBroker, clock: FakeClock
) -> None:
    m = broker.request(_maint(account_id="glm-a"))
    assert isinstance(m, WorkLease)
    clock.advance(broker.policy.lease_ttl_seconds + 1)
    with pytest.raises(StaleWorkLease):
        broker.begin_critical_section(m.lease_id, m.fencing_token)


def test_release_with_stale_token_rejected(broker: WorkBroker) -> None:
    d = broker.request(_default(account_id="glm-a"))
    assert isinstance(d, WorkLease)
    with pytest.raises(StaleWorkLease):
        broker.release(d.lease_id, d.fencing_token + 999)


def test_critical_section_maintenance_only(broker: WorkBroker) -> None:
    d = broker.request(_default(account_id="glm-a"))
    assert isinstance(d, WorkLease)
    with pytest.raises(ValueError):
        broker.begin_critical_section(d.lease_id, d.fencing_token)


def test_begin_critical_section_marks_maintenance(broker: WorkBroker) -> None:
    m = broker.request(_maint(account_id="glm-a"))
    assert isinstance(m, WorkLease)
    broker.begin_critical_section(m.lease_id, m.fencing_token)
    assert broker.release(m.lease_id, m.fencing_token) is True
    assert broker.active_leases() == ()


def test_begin_call_protects_default_from_preemption(
    broker: WorkBroker, clock: FakeClock
) -> None:

    d = broker.request(_default(account_id="glm-a", task_id="t"))
    assert isinstance(d, WorkLease)
    _begin(broker, d)
    fg = broker.request(_fg(account_id="glm-a"))
    assert isinstance(fg, WorkLease) is False
    assert fg.reason is DenialReason.SLOT_BUSY


def test_renew_extends_expiry(broker: WorkBroker, clock: FakeClock) -> None:

    fg = broker.request(_fg(account_id="glm-a"))
    assert isinstance(fg, WorkLease)
    clock.advance(broker.policy.lease_ttl_seconds - 10)
    renewed = broker.renew(fg.lease_id, fg.fencing_token)
    assert renewed.expires_at > fg.expires_at

    clock.advance(broker.policy.lease_ttl_seconds - 10)
    _use(broker, renewed, clock=clock)


def test_renew_after_expiry_rejected(broker: WorkBroker, clock: FakeClock) -> None:
    fg = broker.request(_fg(account_id="glm-a"))
    assert isinstance(fg, WorkLease)
    clock.advance(broker.policy.lease_ttl_seconds + 1)
    with pytest.raises(StaleWorkLease):
        broker.renew(fg.lease_id, fg.fencing_token)
