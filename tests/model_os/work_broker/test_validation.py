from __future__ import annotations

from pathlib import Path

import pytest

from trowel_py.model_os.work_broker import (
    BrokerPolicy,
    BudgetDimensions,
    CatchupPolicy,
    WorkBroker,
    WorkKind,
    WorkRequest,
)
from trowel_py.quota.types import (
    Provider,
)
from tests.model_os.work_broker._support import (
    FakeClock,
    _BASE,
    _policy,
)


def test_policy_validation_rejects_bad_values() -> None:
    with pytest.raises(ValueError):
        BrokerPolicy(lease_ttl_seconds=0)
    with pytest.raises(ValueError):
        BrokerPolicy(concurrency_per_account=0)
    with pytest.raises(ValueError):
        BrokerPolicy(default_quota_used_threshold=float("nan"))
    with pytest.raises(ValueError):
        BrokerPolicy(default_quota_used_threshold=150.0)
    with pytest.raises(ValueError):
        BrokerPolicy(default_tick_max_lag_seconds=-1)
    with pytest.raises(ValueError):
        BrokerPolicy(default_cap=BudgetDimensions(calls=-1))
    with pytest.raises(ValueError):
        BrokerPolicy(default_cap=BudgetDimensions(cost=-1.0))
    with pytest.raises(ValueError):
        BrokerPolicy(glm_account_order=("glm-a", " "))
    assert BrokerPolicy(default_quota_used_threshold=90.0)


def test_request_validation_catchup_vs_kind() -> None:

    with pytest.raises(ValueError):
        WorkRequest(
            kind=WorkKind.DEFAULT,
            provider=Provider.GLM,
            catchup=CatchupPolicy.MAINTENANCE_MERGE,
            catchup_scope="s",
            catchup_period="p",
        )
    with pytest.raises(ValueError):
        WorkRequest(
            kind=WorkKind.FOREGROUND,
            provider=Provider.GLM,
            catchup=CatchupPolicy.MAINTENANCE_MERGE,
            catchup_scope="s",
            catchup_period="p",
        )

    with pytest.raises(ValueError):
        WorkRequest(
            kind=WorkKind.MAINTENANCE,
            provider=Provider.GLM,
            catchup=CatchupPolicy.DEFAULT_DROP,
            scheduled_for=_BASE.isoformat(),
        )

    with pytest.raises(ValueError):
        WorkRequest(
            kind=WorkKind.MAINTENANCE,
            provider=Provider.GLM,
            catchup=CatchupPolicy.MAINTENANCE_MERGE,
        )

    WorkRequest(
        kind=WorkKind.FOREGROUND,
        provider=Provider.GLM,
        deadline="2026-07-23T12:00:00+00:00",
    )


def test_timestamp_validation_in_request(db_path: Path, clock: FakeClock) -> None:
    broker = WorkBroker(db_path, policy=_policy(), read_model=None, now_fn=clock)
    broker.open()
    try:
        with pytest.raises(ValueError):
            broker.request(
                WorkRequest(
                    kind=WorkKind.DEFAULT,
                    provider=Provider.GLM,
                    account_id="glm-a",
                    catchup=CatchupPolicy.DEFAULT_DROP,
                    scheduled_for="garbage",
                )
            )
        with pytest.raises(ValueError):
            broker.request(
                WorkRequest(
                    kind=WorkKind.DEFAULT,
                    provider=Provider.GLM,
                    account_id="glm-a",
                    catchup=CatchupPolicy.DEFAULT_DROP,
                    scheduled_for="2026-07-23T00:00:00",
                )
            )
    finally:
        broker.close()


def test_policy_rejects_nan_inf_on_int_axes() -> None:

    with pytest.raises(ValueError):
        BrokerPolicy(default_cap=BudgetDimensions(calls=float("nan")))
    with pytest.raises(ValueError):
        BrokerPolicy(default_cap=BudgetDimensions(tokens=float("inf")))
    with pytest.raises(ValueError):
        BrokerPolicy(default_cap=BudgetDimensions(wall_seconds=float("nan")))
    with pytest.raises(ValueError):
        BrokerPolicy(default_cap=BudgetDimensions(calls=1.5))
    with pytest.raises(ValueError):
        BrokerPolicy(default_cap=BudgetDimensions(calls=True))


def test_default_drop_requires_scheduled_for() -> None:

    with pytest.raises(ValueError):
        WorkRequest(
            kind=WorkKind.DEFAULT,
            provider=Provider.GLM,
            catchup=CatchupPolicy.DEFAULT_DROP,
        )


def test_request_rejects_garbage_deadline() -> None:

    with pytest.raises(ValueError):
        WorkRequest(kind=WorkKind.FOREGROUND, provider=Provider.GLM, deadline="garbage")
    with pytest.raises(ValueError):
        WorkRequest(
            kind=WorkKind.FOREGROUND,
            provider=Provider.GLM,
            deadline="2026-07-23T00:00:00",
        )


def test_policy_is_immutable_and_replaceable(broker: WorkBroker) -> None:
    p = broker.policy
    with pytest.raises(Exception):
        p.default_quota_used_threshold = 50  # type: ignore[misc]
    p2 = p.replace(default_quota_used_threshold=50.0)
    assert p2.default_quota_used_threshold == 50.0
    assert p2 is not p
