from __future__ import annotations

from pathlib import Path

import pytest

from trowel_py.model_os.work_broker import (
    BudgetDimensions,
    CatchupPolicy,
    IdempotencyConflict,
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
    _default,
    _fg,
    _policy,
)


def test_idempotency_is_request_level_not_slot_level(
    db_path: Path, clock: FakeClock
) -> None:

    broker = WorkBroker(
        db_path,
        policy=_policy(concurrency_per_account=2),
        read_model=None,
        now_fn=clock,
    )
    broker.open()
    try:
        first = broker.request(_default(account_id="glm-a", idem="key-1"))
        assert isinstance(first, WorkLease)
        again = broker.request(_default(account_id="glm-a", idem="key-1"))
        assert isinstance(again, WorkLease)
        assert again.lease_id == first.lease_id
        assert len(broker.active_leases()) == 1
    finally:
        broker.close()


def test_idempotency_expired_prior_is_taken_over(
    db_path: Path, clock: FakeClock
) -> None:

    broker = WorkBroker(
        db_path, policy=_policy(default_cap=BudgetDimensions()), now_fn=clock
    )
    broker.open()
    try:
        first = broker.request(_fg(account_id="glm-a", idem="key-1"))
        assert isinstance(first, WorkLease)
        clock.advance(broker.policy.lease_ttl_seconds + 1)
        again = broker.request(_fg(account_id="glm-a", idem="key-1"))
        assert isinstance(again, WorkLease)
        assert again.lease_id != first.lease_id
        assert again.fencing_token > first.fencing_token
    finally:
        broker.close()


def test_idempotency_key_bound_to_request_fingerprint(broker: WorkBroker) -> None:

    first = broker.request(_default(account_id="glm-a", task_id="t1", idem="k"))
    assert isinstance(first, WorkLease)

    with pytest.raises(IdempotencyConflict):
        broker.request(
            WorkRequest(
                kind=WorkKind.FOREGROUND,
                provider=Provider.GLM,
                account_id="glm-b",
                task_id="t2",
                idempotency_key="k",
            )
        )

    again = broker.request(_default(account_id="glm-a", task_id="t1", idem="k"))
    assert isinstance(again, WorkLease)
    assert again.lease_id == first.lease_id


def test_idempotency_catchup_retry_returns_lease(broker: WorkBroker) -> None:

    first = broker.request(
        WorkRequest(
            kind=WorkKind.MAINTENANCE,
            provider=Provider.GLM,
            account_id="glm-a",
            catchup=CatchupPolicy.MAINTENANCE_MERGE,
            catchup_scope="s",
            catchup_period="p",
            idempotency_key="k",
        )
    )
    assert isinstance(first, WorkLease)

    # 幂等检查先于 catchup gate，活跃请求必须返回原 lease。
    again = broker.request(
        WorkRequest(
            kind=WorkKind.MAINTENANCE,
            provider=Provider.GLM,
            account_id="glm-a",
            catchup=CatchupPolicy.MAINTENANCE_MERGE,
            catchup_scope="s",
            catchup_period="p",
            idempotency_key="k",
        )
    )
    assert isinstance(again, WorkLease)
    assert again.lease_id == first.lease_id
