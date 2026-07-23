from __future__ import annotations

import datetime as _dt

from trowel_py.model_os.work_broker import (
    BrokerPolicy,
    BudgetDimensions,
    CatchupPolicy,
    UsageRecord,
    WorkBroker,
    WorkKind,
    WorkLease,
    WorkRequest,
)
from trowel_py.quota.types import (
    Provider,
    QuotaSnapshot,
    QuotaStatus,
    QuotaWindow,
    QuotaWindowKind,
)

_BASE = _dt.datetime(2026, 7, 23, 0, 0, 0, tzinfo=_dt.timezone.utc)


class FakeClock:
    def __init__(self, start: _dt.datetime = _BASE) -> None:
        self._now = start

    def __call__(self) -> _dt.datetime:
        return self._now

    def advance(self, seconds: float) -> None:
        self._now = self._now + _dt.timedelta(seconds=seconds)

    def iso(self) -> str:
        return self._now.isoformat()


def _window(
    used_percent: float, kind: QuotaWindowKind = QuotaWindowKind.WEEKLY
) -> QuotaWindow:
    return QuotaWindow(kind=kind, used_percent=used_percent, resets_at=None, raw={})


def _snapshot(
    account_id: str,
    used_percent: float,
    *,
    kind: QuotaWindowKind = QuotaWindowKind.WEEKLY,
) -> QuotaSnapshot:
    return QuotaSnapshot(
        provider=Provider.GLM,
        account_id=account_id,
        plan_level="max",
        windows=(_window(used_percent, kind),),
        fetched_at=0,
        status=QuotaStatus.OK,
    )


def _policy(**overrides: object) -> BrokerPolicy:
    return BrokerPolicy().replace(**overrides)  # type: ignore[attr-defined]


def _fg(
    *,
    provider: Provider = Provider.GLM,
    account_id: str | None = None,
    task_id: str = "task-fg",
    priority: int = 100,
    idem: str | None = None,
) -> WorkRequest:
    return WorkRequest(
        kind=WorkKind.FOREGROUND,
        provider=provider,
        account_id=account_id,
        task_id=task_id,
        priority=priority,
        idempotency_key=idem,
    )


def _default(
    *,
    provider: Provider = Provider.GLM,
    account_id: str | None = None,
    task_id: str | None = None,
    priority: int = 10,
    idem: str | None = None,
    budget_cap: BudgetDimensions | None = None,
) -> WorkRequest:
    return WorkRequest(
        kind=WorkKind.DEFAULT,
        provider=provider,
        account_id=account_id,
        task_id=task_id,
        priority=priority,
        budget_cap=budget_cap,
        idempotency_key=idem,
    )


def _maint(
    *,
    provider: Provider = Provider.GLM,
    account_id: str | None = None,
    scope: str = "review",
    period: str = "2026-07-22",
    catchup: CatchupPolicy = CatchupPolicy.MAINTENANCE_MERGE,
    scheduled_for: str | None = None,
    idem: str | None = None,
) -> WorkRequest:
    return WorkRequest(
        kind=WorkKind.MAINTENANCE,
        provider=provider,
        account_id=account_id,
        catchup=catchup,
        catchup_scope=scope,
        catchup_period=period,
        scheduled_for=scheduled_for,
        idempotency_key=idem,
    )


def _begin(broker: WorkBroker, lease: WorkLease) -> None:

    broker.begin_call(lease.lease_id, lease.fencing_token)


def _use(
    broker: WorkBroker,
    lease: WorkLease,
    *,
    calls: int = 1,
    input_tokens: int = 1,
    output_tokens: int = 1,
    cost: float | None = None,
    observation_id: str | None = None,
    clock: FakeClock | None = None,
) -> None:

    broker.record_usage(
        lease.lease_id,
        lease.fencing_token,
        UsageRecord(
            calls=calls,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost=cost,
            occurred_at=(clock.iso() if clock else _BASE.isoformat()),
            observation_id=observation_id,
        ),
    )


def _use_and_return(broker: WorkBroker, lease: WorkLease, *, clock: FakeClock):
    return broker.record_usage(
        lease.lease_id,
        lease.fencing_token,
        UsageRecord(calls=1, input_tokens=10, output_tokens=5, occurred_at=clock.iso()),
    )
