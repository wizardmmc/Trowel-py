from __future__ import annotations

from pathlib import Path


from trowel_py.model_os.work_broker import (
    DenialReason,
    WorkBroker,
    WorkLease,
)
from trowel_py.quota.read_model import QuotaReadModel
from trowel_py.quota.types import (
    Provider,
    QuotaSnapshot,
    QuotaStatus,
    QuotaWindowKind,
)
from tests.model_os.work_broker._support import (
    FakeClock,
    _begin,
    _default,
    _fg,
    _policy,
    _snapshot,
    _use,
    _window,
)


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


def test_foreground_prefers_healthy_account(db_path: Path, clock: FakeClock) -> None:

    rm = QuotaReadModel(now_ms=lambda: 0, stale_after_ms=10**9)
    rm.update(_snapshot("glm-a", 99))
    rm.update(_snapshot("glm-b", 10))
    broker = WorkBroker(db_path, policy=_policy(), read_model=rm, now_fn=clock)
    broker.open()
    try:
        lease = broker.request(_fg())
        assert isinstance(lease, WorkLease)
        assert lease.account_id == "glm-b"
    finally:
        broker.close()


def test_foreground_still_runs_when_all_unhealthy(
    db_path: Path, clock: FakeClock
) -> None:

    # Foreground 属于用户任务，额度快照只参与选账号，不能直接阻断。
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


def test_mixed_failure_detail(db_path: Path, clock: FakeClock) -> None:

    # 多账号同时失败时返回最高优先级原因，detail 仍保留全部失败账号。
    rm = QuotaReadModel(now_ms=lambda: 0, stale_after_ms=10**9)
    rm.update(_snapshot("glm-a", 90))
    broker = WorkBroker(db_path, policy=_policy(), read_model=rm, now_fn=clock)
    broker.open()
    try:
        broker.report_rate_limit(Provider.GLM, "glm-b", cooldown_seconds=60)
        denial = broker.request(_default())
        assert isinstance(denial, WorkLease) is False
        assert denial.reason is DenialReason.QUOTA_LOW
        assert denial.ask_human is True
        assert denial.failed_account_id == "glm-a"
        assert "glm-a" in denial.detail and "glm-b" in denial.detail
    finally:
        broker.close()


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
            provider=Provider.GLM,
            account_id="glm-a",
            plan_level="max",
            windows=(_window(99, QuotaWindowKind.WEB_SEARCHES_MONTHLY),),
            fetched_at=0,
            status=QuotaStatus.OK,
        )
    )
    broker = WorkBroker(db_path, policy=_policy(), read_model=rm, now_fn=clock)
    broker.open()
    try:
        lease = broker.request(_default(account_id="glm-a"))
        assert isinstance(lease, WorkLease)
    finally:
        broker.close()


def test_foreground_does_not_prefer_no_data_account(
    db_path: Path, clock: FakeClock
) -> None:

    # 无快照表示额度未知，不能排在已知健康账号之前。
    rm = QuotaReadModel(now_ms=lambda: 0, stale_after_ms=10**9)
    rm.update(_snapshot("glm-b", 10))
    broker = WorkBroker(db_path, policy=_policy(), read_model=rm, now_fn=clock)
    broker.open()
    try:
        lease = broker.request(_fg())
        assert isinstance(lease, WorkLease)
        assert lease.account_id == "glm-b"
    finally:
        broker.close()
