"""WorkBroker 仲裁测试。

只跑 fake clock + fake 用量 + 崩溃重启：不联网、不发真模型调用、不睡墙钟。
broker 本身是库组件，接入三个真实 scheduler 是另一个阶段的工作，不在这里测。

覆盖的行为分组（行为名 → 代表测试）：

- 过期 lease 拒绝 fenced 写 ......... test_expired_lease_rejects_*
- 临界区只允许 maintenance ......... test_critical_section_*
- begin_call 在调用前标记 started ... test_begin_call_*
- 请求级（非槽级）幂等 + 过期接管 ... test_idempotency_*
- 用量维度从 lease 派生 ............. test_usage_dimensions_from_lease
- cap 只能收窄、不能放宽 ............ test_budget_cap_only_narrows
- 未知费用保持未知 ................. test_unknown_cost_*
- policy 校验 ....................... test_policy_validation_*
- catchup claim/complete + 崩溃自愈 .. test_catchup_*
- catchup×kind 校验 + deadline 字段 . test_request_validation
- foreground 按健康度 failover ...... test_foreground_prefers_healthy
- 混合失败 detail ................... test_mixed_failure_detail
- 严格时间戳校验 ................... test_timestamp_validation
- 跨连接持久化（防幽灵 lease）...... test_catchup_self_heal_is_durable_across_connections
"""

from __future__ import annotations

import datetime as _dt
from pathlib import Path

import pytest

from trowel_py.model_os.work_broker import (
    BrokerPolicy,
    BudgetDimensions,
    CatchupPolicy,
    DenialReason,
    IdempotencyConflict,
    StaleWorkLease,
    UsageRecord,
    WorkBroker,
    WorkKind,
    WorkLease,
    WorkRequest,
)
from trowel_py.quota.read_model import QuotaReadModel
from trowel_py.quota.types import (
    Provider,
    QuotaSnapshot,
    QuotaStatus,
    QuotaWindow,
    QuotaWindowKind,
)

_BASE = _dt.datetime(2026, 7, 23, 0, 0, 0, tzinfo=_dt.timezone.utc)


class FakeClock:
    """可注入 broker 的可控 UTC 时钟。"""

    def __init__(self, start: _dt.datetime = _BASE) -> None:
        self._now = start

    def __call__(self) -> _dt.datetime:
        return self._now

    def advance(self, seconds: float) -> None:
        self._now = self._now + _dt.timedelta(seconds=seconds)

    def iso(self) -> str:
        return self._now.isoformat()


def _window(used_percent: float, kind: QuotaWindowKind = QuotaWindowKind.WEEKLY) -> QuotaWindow:
    return QuotaWindow(kind=kind, used_percent=used_percent, resets_at=None, raw={})


def _snapshot(
    account_id: str, used_percent: float, *, kind: QuotaWindowKind = QuotaWindowKind.WEEKLY
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


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "workbroker.db"


@pytest.fixture
def broker(db_path: Path, clock: FakeClock) -> WorkBroker:
    """固定 fake clock、无 quota read model（额度闸门跳过）的 broker。"""

    b = WorkBroker(db_path, policy=_policy(), read_model=None, now_fn=clock)
    b.open()
    yield b
    b.close()


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
    """把 lease 标 started（发起 native 调用前调）。失效则抛错。"""

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
    """在某 lease 上记一笔用量（归因维度来自 lease）。"""

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


# ----------------------------------------------------- 优先级与并发上限


def test_concurrency_cap_one_slot_per_account(broker: WorkBroker) -> None:
    first = broker.request(_default(account_id="glm-a"))
    assert isinstance(first, WorkLease)
    second = broker.request(_default(account_id="glm-a"))
    assert isinstance(second, WorkLease) is False
    assert second.reason is DenialReason.SLOT_BUSY
    assert second.retry_after_seconds is not None
    other = broker.request(_default(account_id="glm-b"))
    assert isinstance(other, WorkLease)


def test_foreground_preempts_unstarted_default(broker: WorkBroker) -> None:
    d = broker.request(_default(account_id="glm-a"))
    assert isinstance(d, WorkLease)
    fg = broker.request(_fg(account_id="glm-a"))
    assert isinstance(fg, WorkLease)
    assert fg.slot == d.slot
    assert all(lse.lease_id != d.lease_id for lse in broker.active_leases())


def test_foreground_cannot_preempt_started_default(
    broker: WorkBroker, clock: FakeClock
) -> None:
    """begin_call 后 default 已发出 native 调用，不可被抢占。"""

    d = broker.request(_default(account_id="glm-a", task_id="task-d"))
    assert isinstance(d, WorkLease)
    _begin(broker, d)  # 即将发起 native 调用 -> running
    fg = broker.request(_fg(account_id="glm-a"))
    assert isinstance(fg, WorkLease) is False
    assert fg.reason is DenialReason.SLOT_BUSY


def test_foreground_cannot_preempt_maintenance(broker: WorkBroker) -> None:
    m = broker.request(_maint(account_id="glm-a"))
    assert isinstance(m, WorkLease)
    fg = broker.request(_fg(account_id="glm-a"))
    assert isinstance(fg, WorkLease) is False
    assert fg.reason is DenialReason.SLOT_BUSY


def test_account_failover_on_busy_slot(broker: WorkBroker, clock: FakeClock) -> None:
    d = broker.request(_default(account_id="glm-a", task_id="task-d"))
    assert isinstance(d, WorkLease)
    _begin(broker, d)
    fg = broker.request(_fg())  # no account_id -> broker picks across the order
    assert isinstance(fg, WorkLease)
    assert fg.account_id == "glm-b"


# ------------------------------------------------------- catchup（补跑）


def test_maintenance_catchup_live_then_merge(broker: WorkBroker) -> None:
    """同一 `(scope, period)` 在首个 lease 仍活跃时，第二次请求被合并丢弃
    （CATCHUP_ALREADY_DONE）。"""

    first = broker.request(_maint())
    assert isinstance(first, WorkLease)
    second = broker.request(_maint())
    assert isinstance(second, WorkLease) is False
    assert second.reason is DenialReason.CATCHUP_ALREADY_DONE


def test_maintenance_catchup_completes_on_complete(broker: WorkBroker) -> None:
    """成功跑完调 `complete` → `(scope, period)` 标 COMPLETED；后续请求被合并丢弃。"""

    first = broker.request(_maint(period="2026-07-22"))
    assert isinstance(first, WorkLease)
    assert broker.complete(first.lease_id, first.fencing_token) is True
    again = broker.request(_maint(period="2026-07-22"))
    assert isinstance(again, WorkLease) is False
    assert again.reason is DenialReason.CATCHUP_ALREADY_DONE


def test_maintenance_abandon_does_not_complete(broker: WorkBroker) -> None:
    """`release`（放弃）**不**推进 catch-up——失败/放弃的 maintenance 留 claim 自愈，必要工作重跑。"""

    first = broker.request(_maint(period="2026-07-22"))
    assert isinstance(first, WorkLease)
    assert broker.release(first.lease_id, first.fencing_token) is True  # abandon
    again = broker.request(_maint(period="2026-07-22"))
    assert isinstance(again, WorkLease)  # re-runs, not merged away


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


# --------------------------------------- catchup 崩溃自愈


def test_catchup_crash_self_heals(db_path: Path, clock: FakeClock) -> None:
    """lease 已死（release 前崩溃）的 claim 不会永久挡路：下次请求清死 claim 重新 grant，
    必要维护不会丢。"""

    b1 = WorkBroker(db_path, policy=_policy(), read_model=None, now_fn=clock)
    b1.open()
    first = b1.request(_maint(period="2026-07-20"))
    assert isinstance(first, WorkLease)
    b1.close()  # crash — lease never released, claim stays 'claimed'

    clock.advance(b1.policy.lease_ttl_seconds + 5)  # past expiry
    b2 = WorkBroker(db_path, policy=_policy(), read_model=None, now_fn=clock)
    reclaimed = b2.open_recover()
    assert reclaimed >= 1
    # the dead claim self-heals -> the missed maintenance runs again
    redo = b2.request(_maint(period="2026-07-20"))
    assert isinstance(redo, WorkLease)
    b2.close()


# --------------------------------------- 三种 denial 原因可区分


def test_denial_rate_limit(broker: WorkBroker) -> None:
    broker.report_rate_limit(Provider.GLM, "glm-a", cooldown_seconds=60)
    broker.report_rate_limit(Provider.GLM, "glm-b", cooldown_seconds=60)
    denial = broker.request(_fg())
    assert isinstance(denial, WorkLease) is False
    assert denial.reason is DenialReason.RATE_LIMIT


def test_denial_budget_exhausted(broker: WorkBroker, clock: FakeClock) -> None:
    cap = broker.policy.default_cap
    assert cap.calls is not None
    d = broker.request(_default(account_id="glm-a", task_id="t1"))
    assert isinstance(d, WorkLease)
    _use(broker, d, calls=cap.calls, clock=clock)
    broker.release(d.lease_id, d.fencing_token)
    denial = broker.request(_default(account_id="glm-a"))
    assert isinstance(denial, WorkLease) is False
    assert denial.reason is DenialReason.BUDGET_EXHAUSTED


def test_denial_slot_contention(broker: WorkBroker, clock: FakeClock) -> None:
    fa = broker.request(_fg(account_id="glm-a", task_id="ta"))
    fb = broker.request(_fg(account_id="glm-b", task_id="tb"))
    assert isinstance(fa, WorkLease) and isinstance(fb, WorkLease)
    _begin(broker, fa)
    _begin(broker, fb)
    denial = broker.request(_default())
    assert isinstance(denial, WorkLease) is False
    assert denial.reason is DenialReason.SLOT_BUSY


# ------------------------------------------------------- 崩溃恢复与 fencing


def test_crash_recovery_reclaims_expired_lease(
    db_path: Path, clock: FakeClock
) -> None:
    b1 = WorkBroker(
        db_path, policy=_policy(default_cap=BudgetDimensions()), read_model=None, now_fn=clock
    )
    b1.open()
    dead = b1.request(_fg(account_id="glm-a"))
    assert isinstance(dead, WorkLease)
    b1.close()

    clock.advance(b1.policy.lease_ttl_seconds + 5)
    b2 = WorkBroker(
        db_path, policy=_policy(default_cap=BudgetDimensions()), read_model=None, now_fn=clock
    )
    reclaimed = b2.open_recover()
    assert reclaimed >= 1
    fresh = b2.request(_fg(account_id="glm-a"))
    assert isinstance(fresh, WorkLease)
    assert fresh.fencing_token > dead.fencing_token
    b2.close()


def test_crash_recovery_old_fencing_token_dead(
    db_path: Path, clock: FakeClock
) -> None:
    b1 = WorkBroker(
        db_path, policy=_policy(default_cap=BudgetDimensions()), read_model=None, now_fn=clock
    )
    b1.open()
    dead = b1.request(_fg(account_id="glm-a", task_id="t-old"))
    assert isinstance(dead, WorkLease)
    b1.close()

    clock.advance(b1.policy.lease_ttl_seconds + 5)
    b2 = WorkBroker(
        db_path, policy=_policy(default_cap=BudgetDimensions()), read_model=None, now_fn=clock
    )
    b2.open_recover()
    fresh = b2.request(_fg(account_id="glm-a", task_id="t-new"))
    assert isinstance(fresh, WorkLease)
    with pytest.raises(StaleWorkLease):
        _use(b2, dead, clock=clock)  # old token + old lease -> rejected
    totals = _use_and_return(b2, fresh, clock=clock)
    assert totals.calls == 1
    b2.close()


def _use_and_return(broker: WorkBroker, lease: WorkLease, *, clock: FakeClock):
    return broker.record_usage(
        lease.lease_id, lease.fencing_token,
        UsageRecord(calls=1, input_tokens=10, output_tokens=5, occurred_at=clock.iso()),
    )


# --------------------------------------- 过期 lease 拒绝 fenced 写


def test_expired_lease_rejects_record_usage(broker: WorkBroker, clock: FakeClock) -> None:
    """TTL 已过（但还没被扫）的 lease 不接受记账——不给过期持有者留记账窗口。"""

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


# --------------------------------------- 临界区只允许 maintenance


def test_critical_section_maintenance_only(broker: WorkBroker) -> None:
    d = broker.request(_default(account_id="glm-a"))
    assert isinstance(d, WorkLease)
    with pytest.raises(ValueError):
        broker.begin_critical_section(d.lease_id, d.fencing_token)


def test_begin_critical_section_marks_maintenance(broker: WorkBroker) -> None:
    m = broker.request(_maint(account_id="glm-a"))
    assert isinstance(m, WorkLease)
    broker.begin_critical_section(m.lease_id, m.fencing_token)  # maintenance-only
    assert broker.release(m.lease_id, m.fencing_token) is True
    assert broker.active_leases() == ()


# --------------------------------------- begin_call 在调用前标记 started


def test_begin_call_protects_default_from_preemption(
    broker: WorkBroker, clock: FakeClock
) -> None:
    """begin_call（而非 record_usage）是「已发出 native 调用」的信号；begin 过的 default 不可抢占。"""

    d = broker.request(_default(account_id="glm-a", task_id="t"))
    assert isinstance(d, WorkLease)
    _begin(broker, d)  # 还没记账，但调用已发出
    fg = broker.request(_fg(account_id="glm-a"))
    assert isinstance(fg, WorkLease) is False
    assert fg.reason is DenialReason.SLOT_BUSY


# --------------------------------------- 请求级幂等


def test_idempotency_is_request_level_not_slot_level(
    db_path: Path, clock: FakeClock
) -> None:
    """concurrency=2 时同 key 重试回收同一 lease，而不是抢第二个槽（槽是 broker 选的）。"""

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
        assert again.lease_id == first.lease_id  # same lease, not a second slot
        assert len(broker.active_leases()) == 1
    finally:
        broker.close()


def test_idempotency_expired_prior_is_taken_over(
    db_path: Path, clock: FakeClock
) -> None:
    """上次 grant 已过期的重试：接管出新 lease + 更高 token（不会永久 SLOT_BUSY）。"""

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


# --------------------------------------- 用量维度从 lease 派生


def test_usage_dimensions_from_lease(broker: WorkBroker, clock: FakeClock) -> None:
    """持有者伪造不了 provider/kind/task——用量维度来自 lease 行，不是调用方的
    `UsageRecord`（它只带量）。"""

    fg = broker.request(_fg(account_id="glm-a", task_id="t-real"))
    assert isinstance(fg, WorkLease)
    _use(broker, fg, calls=3, clock=clock)
    # attributed to the lease's GLM / foreground / t-real
    assert broker.usage_totals(provider=Provider.GLM).calls == 3
    assert broker.usage_totals(task_id="t-real").calls == 3
    assert broker.usage_totals(work_kind=WorkKind.FOREGROUND).calls == 3
    # cannot be attributed to a different provider/kind/task
    assert broker.usage_totals(provider=Provider.CODEX).calls == 0
    assert broker.usage_totals(work_kind=WorkKind.DEFAULT).calls == 0
    assert broker.usage_totals(task_id="t-forged").calls == 0


def test_usage_attribution_and_observation_idempotency(
    broker: WorkBroker, clock: FakeClock
) -> None:
    """用量可按 kind/account/day 归因；重复的 observation_id 不重复记账（重试安全）。"""

    d = broker.request(_default(account_id="glm-a", task_id="task-1"))
    assert isinstance(d, WorkLease)
    _use(broker, d, calls=3, cost=0.5, observation_id="obs-1", clock=clock)
    _use(broker, d, calls=3, cost=0.5, observation_id="obs-1", clock=clock)  # retry
    assert broker.usage_totals(work_kind=WorkKind.DEFAULT).calls == 3  # not 6
    assert broker.usage_totals(account_id="glm-a").cost == pytest.approx(0.5)


def test_usage_rejects_negative_and_garbage(broker: WorkBroker, clock: FakeClock) -> None:
    d = broker.request(_default(account_id="glm-a", task_id="t"))
    assert isinstance(d, WorkLease)
    with pytest.raises(ValueError):
        broker.record_usage(
            d.lease_id, d.fencing_token,
            UsageRecord(calls=-1, occurred_at=clock.iso()),
        )
    with pytest.raises(ValueError):
        broker.record_usage(
            d.lease_id, d.fencing_token,
            UsageRecord(calls=1, occurred_at="garbage"),
        )
    with pytest.raises(ValueError):
        broker.record_usage(
            d.lease_id, d.fencing_token,
            UsageRecord(calls=1, occurred_at="2026-07-23T00:00:00"),  # naive
        )


# --------------------------------------- cap 只收窄、不放宽


def test_budget_cap_only_narrows(broker: WorkBroker) -> None:
    """default 的 granted cap 是 policy cap 被请求收窄后的结果——请求只能让它更紧、
    不能更松，空 cap 也不能抬高 policy 限制。"""

    # 收窄：请求 50 vs policy 100 -> granted 50
    narrow = broker.request(_default(account_id="glm-a", budget_cap=BudgetDimensions(calls=50)))
    assert isinstance(narrow, WorkLease)
    assert narrow.granted_cap == BudgetDimensions(calls=50)
    broker.release(narrow.lease_id, narrow.fencing_token)
    # widen attempt: request 200 vs policy 100 -> still 100 (cannot self-raise)
    widen = broker.request(_default(account_id="glm-b", budget_cap=BudgetDimensions(calls=200)))
    assert isinstance(widen, WorkLease)
    assert widen.granted_cap == BudgetDimensions(calls=100)
    broker.release(widen.lease_id, widen.fencing_token)
    # empty cap does not lift the policy limit
    empty = broker.request(_default(account_id="glm-a", budget_cap=BudgetDimensions()))
    assert isinstance(empty, WorkLease)
    assert empty.granted_cap == BudgetDimensions(calls=100)


def test_budget_gate_uses_narrowed_cap(broker: WorkBroker, clock: FakeClock) -> None:
    """收窄了 cap 的请求受更紧的数字约束。"""

    d = broker.request(_default(account_id="glm-a", task_id="t", budget_cap=BudgetDimensions(calls=2)))
    assert isinstance(d, WorkLease)
    _use(broker, d, calls=2, clock=clock)
    broker.release(d.lease_id, d.fencing_token)
    denial = broker.request(
        _default(account_id="glm-a", budget_cap=BudgetDimensions(calls=2))
    )
    assert isinstance(denial, WorkLease) is False
    assert denial.reason is DenialReason.BUDGET_EXHAUSTED
    # but policy cap (100) is still wide open for a request that does not narrow
    other = broker.request(_default(account_id="glm-a", task_id="t2"))
    assert isinstance(other, WorkLease)


# --------------------------------------- 未知费用保持未知


def test_unknown_cost_stays_unknown(broker: WorkBroker, clock: FakeClock) -> None:
    """费用未知的用量保持 `UsageTotals.cost` 为 None（不折成 0）；费用 cap 不被未知费用误触发。"""

    d = broker.request(_default(account_id="glm-a", task_id="t"))
    assert isinstance(d, WorkLease)
    _use(broker, d, calls=1, cost=None, clock=clock)
    totals = broker.usage_totals(work_kind=WorkKind.DEFAULT)
    assert totals.cost is None  # unknown, not 0


def test_known_and_unknown_cost_yields_unknown(broker: WorkBroker, clock: FakeClock) -> None:
    d = broker.request(_default(account_id="glm-a", task_id="t"))
    assert isinstance(d, WorkLease)
    _use(broker, d, calls=1, cost=0.3, observation_id="a", clock=clock)
    _use(broker, d, calls=1, cost=None, observation_id="b", clock=clock)
    assert broker.usage_totals(work_kind=WorkKind.DEFAULT).cost is None


def test_cost_cap_not_falsely_tripped_by_unknown(
    db_path: Path, clock: FakeClock
) -> None:
    """费用 cap 在费用未知时不阻断（未知保持未知，不当成 0 也不当成越线）。"""

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
        _use(broker, d, cost=None, clock=clock)  # unknown
        broker.release(d.lease_id, d.fencing_token)
        # unknown cost must not exhaust the cap (nor falsely clear it)
        nxt = broker.request(_default(account_id="glm-a", task_id="t2"))
        assert isinstance(nxt, WorkLease)
    finally:
        broker.close()


# --------------------------------------- policy 校验


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
    assert BrokerPolicy(default_quota_used_threshold=90.0)  # valid


# --------------------------------------- 请求字段校验


def test_request_validation_catchup_vs_kind() -> None:
    # MAINTENANCE_MERGE only legal for maintenance
    with pytest.raises(ValueError):
        WorkRequest(
            kind=WorkKind.DEFAULT, provider=Provider.GLM,
            catchup=CatchupPolicy.MAINTENANCE_MERGE, catchup_scope="s", catchup_period="p",
        )
    with pytest.raises(ValueError):
        WorkRequest(
            kind=WorkKind.FOREGROUND, provider=Provider.GLM,
            catchup=CatchupPolicy.MAINTENANCE_MERGE, catchup_scope="s", catchup_period="p",
        )
    # DEFAULT_DROP only legal for default
    with pytest.raises(ValueError):
        WorkRequest(
            kind=WorkKind.MAINTENANCE, provider=Provider.GLM,
            catchup=CatchupPolicy.DEFAULT_DROP, scheduled_for=_BASE.isoformat(),
        )
    # MERGE requires scope + period
    with pytest.raises(ValueError):
        WorkRequest(kind=WorkKind.MAINTENANCE, provider=Provider.GLM, catchup=CatchupPolicy.MAINTENANCE_MERGE)
    # legal ones construct fine (deadline is an accepted reserved field)
    WorkRequest(
        kind=WorkKind.FOREGROUND, provider=Provider.GLM, deadline="2026-07-23T12:00:00+00:00"
    )


# --------------------------------------- foreground 按健康度 failover


def test_foreground_prefers_healthy_account(
    db_path: Path, clock: FakeClock
) -> None:
    """foreground 不被低额度阻断，但 failover 空闲时优先健康账号。"""

    rm = QuotaReadModel(now_ms=lambda: 0, stale_after_ms=10**9)
    rm.update(_snapshot("glm-a", 99))  # near full
    rm.update(_snapshot("glm-b", 10))  # healthy
    broker = WorkBroker(db_path, policy=_policy(), read_model=rm, now_fn=clock)
    broker.open()
    try:
        lease = broker.request(_fg())  # unpinned -> broker orders by health
        assert isinstance(lease, WorkLease)
        assert lease.account_id == "glm-b"
    finally:
        broker.close()


def test_foreground_still_runs_when_all_unhealthy(
    db_path: Path, clock: FakeClock
) -> None:
    """foreground 是用户的任务——即便所有账号都快满也照跑（运行时的 provider 限流才是刹车，不是快照）。"""

    rm = QuotaReadModel(now_ms=lambda: 0, stale_after_ms=10**9)
    rm.update(_snapshot("glm-a", 99))
    rm.update(_snapshot("glm-b", 95))
    broker = WorkBroker(db_path, policy=_policy(), read_model=rm, now_fn=clock)
    broker.open()
    try:
        lease = broker.request(_fg(account_id="glm-a"))
        assert isinstance(lease, WorkLease)
    finally:
        broker.close()


# --------------------------------------- 混合失败 detail


def test_mixed_failure_detail(
    db_path: Path, clock: FakeClock
) -> None:
    """一个账号额度低、另一个限流的 default：返回优先级最高的原因（QUOTA_LOW），
    但 detail 列出两个账号，failed_account_id 也被赋值。"""

    rm = QuotaReadModel(now_ms=lambda: 0, stale_after_ms=10**9)
    rm.update(_snapshot("glm-a", 90))  # quota-low
    broker = WorkBroker(db_path, policy=_policy(), read_model=rm, now_fn=clock)
    broker.open()
    try:
        broker.report_rate_limit(Provider.GLM, "glm-b", cooldown_seconds=60)
        denial = broker.request(_default())  # glm-a quota_low, glm-b rate_limit
        assert isinstance(denial, WorkLease) is False
        assert denial.reason is DenialReason.QUOTA_LOW
        assert denial.ask_human is True
        assert denial.failed_account_id == "glm-a"
        assert "glm-a" in denial.detail and "glm-b" in denial.detail
    finally:
        broker.close()


# --------------------------------------- 严格时间戳校验


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
                    scheduled_for="2026-07-23T00:00:00",  # naive
                )
            )
    finally:
        broker.close()


# --------------------------------------- foreground 无内部 cap + default 额度闸门


def test_foreground_ignores_internal_budget_cap(
    broker: WorkBroker, clock: FakeClock
) -> None:
    cap = broker.policy.default_cap
    d = broker.request(_default(account_id="glm-a", task_id="t"))
    assert isinstance(d, WorkLease)
    _use(broker, d, calls=(cap.calls or 0) * 10, clock=clock)
    broker.release(d.lease_id, d.fencing_token)
    fg = broker.request(_fg(account_id="glm-a"))
    assert isinstance(fg, WorkLease)


def test_default_denied_when_quota_low_ask_human(
    db_path: Path, clock: FakeClock
) -> None:
    rm = QuotaReadModel(now_ms=lambda: 0, stale_after_ms=10**9)
    rm.update(_snapshot("glm-a", 90))
    rm.update(_snapshot("glm-b", 95))
    broker = WorkBroker(db_path, policy=_policy(), read_model=rm, now_fn=clock)
    broker.open()
    try:
        denial = broker.request(_default())
        assert isinstance(denial, WorkLease) is False
        assert denial.reason is DenialReason.QUOTA_LOW
        assert denial.ask_human is True
    finally:
        broker.close()


def test_default_quota_gate_ask_human_off(db_path: Path, clock: FakeClock) -> None:
    rm = QuotaReadModel(now_ms=lambda: 0, stale_after_ms=10**9)
    rm.update(_snapshot("glm-a", 90))
    rm.update(_snapshot("glm-b", 95))
    broker = WorkBroker(
        db_path,
        policy=_policy(default_ask_human_on_quota_sensitive=False),
        read_model=rm,
        now_fn=clock,
    )
    broker.open()
    try:
        denial = broker.request(_default())
        assert isinstance(denial, WorkLease) is False
        assert denial.reason is DenialReason.QUOTA_LOW
        assert denial.ask_human is False
    finally:
        broker.close()


def test_default_runs_when_quota_healthy(db_path: Path, clock: FakeClock) -> None:
    rm = QuotaReadModel(now_ms=lambda: 0, stale_after_ms=10**9)
    rm.update(_snapshot("glm-a", 30))
    broker = WorkBroker(db_path, policy=_policy(), read_model=rm, now_fn=clock)
    broker.open()
    try:
        lease = broker.request(_default(account_id="glm-a"))
        assert isinstance(lease, WorkLease)
    finally:
        broker.close()


def test_quota_gate_ignores_web_search_window(db_path: Path, clock: FakeClock) -> None:
    rm = QuotaReadModel(now_ms=lambda: 0, stale_after_ms=10**9)
    rm.update(
        QuotaSnapshot(
            provider=Provider.GLM, account_id="glm-a", plan_level="max",
            windows=(_window(99, QuotaWindowKind.WEB_SEARCHES_MONTHLY),),
            fetched_at=0, status=QuotaStatus.OK,
        )
    )
    broker = WorkBroker(db_path, policy=_policy(), read_model=rm, now_fn=clock)
    broker.open()
    try:
        lease = broker.request(_default(account_id="glm-a"))
        assert isinstance(lease, WorkLease)  # model-call quota untouched
    finally:
        broker.close()


# --------------------------------------- 其它边界（幂等指纹 / renew / 跨连接持久化 等）


def test_catchup_self_heal_is_durable_across_connections(
    db_path: Path, clock: FakeClock
) -> None:
    """跨连接持久化回归：自愈重新 grant 的路径必须把新 lease 真正提交。
    若清死 claim 的 DELETE 落在事务外、开了隐式事务，仲裁的 _tx 会误判成嵌套而不提交，
    新 lease 就只在当前连接可见、close 时被回滚。本测试用第二个连接逼它提交。"""

    b1 = WorkBroker(db_path, policy=_policy(), read_model=None, now_fn=clock)
    b1.open()
    first = b1.request(_maint(period="2026-07-20"))
    assert isinstance(first, WorkLease)
    b1.close()  # 模拟崩溃——claim 留 claimed，lease 没 release

    clock.advance(b1.policy.lease_ttl_seconds + 5)
    b2 = WorkBroker(db_path, policy=_policy(), read_model=None, now_fn=clock)
    b2.open_recover()
    redo = b2.request(_maint(period="2026-07-20"))  # 自愈重新 grant
    assert isinstance(redo, WorkLease)

    # 同库的新连接必须看到这份 re-grant 的 lease——即自愈的 grant 已提交，
    # 不是 b2 那个未提交事务里的幽灵。
    b3 = WorkBroker(db_path, policy=_policy(), read_model=None, now_fn=clock)
    b3.open()
    try:
        active_ids = {lse.lease_id for lse in b3.active_leases()}
        assert redo.lease_id in active_ids
    finally:
        b3.close()
        b2.close()


def test_idempotency_key_bound_to_request_fingerprint(broker: WorkBroker) -> None:
    """重试 key 被复用在另一个请求（kind/account 不同）上应被拒绝，
    而不是静默复用上次的 grant。"""

    first = broker.request(_default(account_id="glm-a", task_id="t1", idem="k"))
    assert isinstance(first, WorkLease)
    # 同 key、不同 account/task -> 冲突
    with pytest.raises(IdempotencyConflict):
        broker.request(
            WorkRequest(
                kind=WorkKind.FOREGROUND, provider=Provider.GLM,
                account_id="glm-b", task_id="t2", idempotency_key="k",
            )
        )
    # 同 key、同请求形状 -> 回收同一 lease（不冲突）
    again = broker.request(_default(account_id="glm-a", task_id="t1", idem="k"))
    assert isinstance(again, WorkLease)
    assert again.lease_id == first.lease_id


def test_idempotency_catchup_retry_returns_lease(broker: WorkBroker) -> None:
    """活跃 lease 的补跑重试返回的是 LEASE（幂等），而不是 CATCHUP_ALREADY_DONE——
    幂等检查在 catch-up gate 之前。"""

    first = broker.request(
        WorkRequest(
            kind=WorkKind.MAINTENANCE, provider=Provider.GLM, account_id="glm-a",
            catchup=CatchupPolicy.MAINTENANCE_MERGE, catchup_scope="s",
            catchup_period="p", idempotency_key="k",
        )
    )
    assert isinstance(first, WorkLease)
    again = broker.request(
        WorkRequest(
            kind=WorkKind.MAINTENANCE, provider=Provider.GLM, account_id="glm-a",
            catchup=CatchupPolicy.MAINTENANCE_MERGE, catchup_scope="s",
            catchup_period="p", idempotency_key="k",
        )
    )
    assert isinstance(again, WorkLease)
    assert again.lease_id == first.lease_id


def test_renew_extends_expiry(broker: WorkBroker, clock: FakeClock) -> None:
    """renew 把 expires_at 推后，让长 native 调用保住槽。"""

    fg = broker.request(_fg(account_id="glm-a"))
    assert isinstance(fg, WorkLease)
    clock.advance(broker.policy.lease_ttl_seconds - 10)  # 接近过期
    renewed = broker.renew(fg.lease_id, fg.fencing_token)
    assert renewed.expires_at > fg.expires_at
    # 原 lease 本该过期之后，续约过的 lease 仍可写
    clock.advance(broker.policy.lease_ttl_seconds - 10)
    _use(broker, renewed, clock=clock)  # 不抛


def test_renew_after_expiry_rejected(broker: WorkBroker, clock: FakeClock) -> None:
    fg = broker.request(_fg(account_id="glm-a"))
    assert isinstance(fg, WorkLease)
    clock.advance(broker.policy.lease_ttl_seconds + 1)
    with pytest.raises(StaleWorkLease):
        broker.renew(fg.lease_id, fg.fencing_token)


def test_policy_rejects_nan_inf_on_int_axes() -> None:
    """整数 cap 轴上的 NaN/Infinity 必须被拒（NaN < 0 恒 False，单纯非负检查会放行）。"""

    with pytest.raises(ValueError):
        BrokerPolicy(default_cap=BudgetDimensions(calls=float("nan")))
    with pytest.raises(ValueError):
        BrokerPolicy(default_cap=BudgetDimensions(tokens=float("inf")))
    with pytest.raises(ValueError):
        BrokerPolicy(default_cap=BudgetDimensions(wall_seconds=float("nan")))
    with pytest.raises(ValueError):
        BrokerPolicy(default_cap=BudgetDimensions(calls=1.5))  # 整数轴给 float
    with pytest.raises(ValueError):
        BrokerPolicy(default_cap=BudgetDimensions(calls=True))  # bool


def test_foreground_does_not_prefer_no_data_account(
    db_path: Path, clock: FakeClock
) -> None:
    """无数据账号是 unknown、不是 healthy——foreground 不能把它排在已知健康账号前面。"""

    rm = QuotaReadModel(now_ms=lambda: 0, stale_after_ms=10**9)
    rm.update(_snapshot("glm-b", 10))  # glm-a 无快照（unknown）
    broker = WorkBroker(db_path, policy=_policy(), read_model=rm, now_fn=clock)
    broker.open()
    try:
        lease = broker.request(_fg())
        assert isinstance(lease, WorkLease)
        assert lease.account_id == "glm-b"  # 已知健康胜过 unknown 的 glm-a
    finally:
        broker.close()


def test_default_drop_requires_scheduled_for() -> None:
    """漏掉的 default tick 没带触发时刻是无意义的——必须强制 scheduled_for。"""

    with pytest.raises(ValueError):
        WorkRequest(
            kind=WorkKind.DEFAULT, provider=Provider.GLM,
            catchup=CatchupPolicy.DEFAULT_DROP,  # no scheduled_for
        )


def test_request_rejects_garbage_deadline() -> None:
    """deadline 虽是预留字段，但仍是时间戳——拒绝非法值。"""

    with pytest.raises(ValueError):
        WorkRequest(
            kind=WorkKind.FOREGROUND, provider=Provider.GLM, deadline="garbage"
        )
    with pytest.raises(ValueError):
        WorkRequest(
            kind=WorkKind.FOREGROUND, provider=Provider.GLM,
            deadline="2026-07-23T00:00:00",  # naive
        )


def test_observation_id_scoped_per_lease(
    broker: WorkBroker, clock: FakeClock
) -> None:
    """observation_id 作用域是 `(lease_id, observation_id)`——两个不同 lease
    可复用同一 id，不会一个静默吞掉另一个。"""

    a = broker.request(_default(account_id="glm-a", task_id="ta"))
    b = broker.request(_default(account_id="glm-b", task_id="tb"))
    assert isinstance(a, WorkLease) and isinstance(b, WorkLease)
    _use(broker, a, calls=3, observation_id="obs-1", clock=clock)
    _use(broker, b, calls=7, observation_id="obs-1", clock=clock)  # 同 id、不同 lease
    assert broker.usage_totals(task_id="ta").calls == 3
    assert broker.usage_totals(task_id="tb").calls == 7


def test_usage_retry_returns_consistent_day(broker: WorkBroker, clock: FakeClock) -> None:
    """带偏移时间戳的重试观测，返回的当日累计与首次插入一致（两边都用 UTC 日）。"""

    d = broker.request(_default(account_id="glm-a", task_id="t"))
    assert isinstance(d, WorkLease)
    offset_ts = "2026-07-23T00:30:00+08:00"  # UTC = 2026-07-22T16:30
    broker.record_usage(
        d.lease_id, d.fencing_token,
        UsageRecord(calls=3, occurred_at=offset_ts, observation_id="obs-x"),
    )
    again = broker.record_usage(  # 重试 -> 不插入，返回累计
        d.lease_id, d.fencing_token,
        UsageRecord(calls=3, occurred_at=offset_ts, observation_id="obs-x"),
    )
    # UTC 当日是 2026-07-22；两个分支下该日累计都应是 3
    assert again.calls == 3
    assert broker.usage_totals(day="2026-07-22").calls == 3
    assert broker.usage_totals(day="2026-07-23").calls == 0


# ---------------------------------------------------- 杂项 / 边界


def test_granted_cap_reflected_on_lease(broker: WorkBroker) -> None:
    fg = broker.request(_fg(account_id="glm-a"))
    assert isinstance(fg, WorkLease)
    assert fg.granted_cap is None
    d = broker.request(_default(account_id="glm-b"))
    assert isinstance(d, WorkLease)
    assert d.granted_cap == broker.policy.default_cap


def test_policy_is_immutable_and_replaceable(broker: WorkBroker) -> None:
    p = broker.policy
    with pytest.raises(Exception):
        p.default_quota_used_threshold = 50  # type: ignore[misc]
    p2 = p.replace(default_quota_used_threshold=50.0)
    assert p2.default_quota_used_threshold == 50.0
    assert p2 is not p


def test_slots_free_after_release(broker: WorkBroker) -> None:
    a = broker.request(_default(account_id="glm-a"))
    assert isinstance(a, WorkLease)
    broker.release(a.lease_id, a.fencing_token)
    b = broker.request(_default(account_id="glm-a"))
    assert isinstance(b, WorkLease)


def test_arbitration_is_atomic_under_concurrent_threads(
    db_path: Path, clock: FakeClock
) -> None:
    import threading

    broker = WorkBroker(db_path, policy=_policy(), read_model=None, now_fn=clock)
    broker.open()
    results: list[object] = []
    lock = threading.Lock()

    def worker() -> None:
        outcome = broker.request(_default(account_id="glm-a"))
        with lock:
            results.append(outcome)

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    leases = [r for r in results if isinstance(r, WorkLease)]
    denials = [r for r in results if not isinstance(r, WorkLease)]
    assert len(leases) == 1
    assert len(denials) == 1
    broker.close()
