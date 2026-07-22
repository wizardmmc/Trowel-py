"""QuotaScheduler cadence / stagger / fault tolerance (slice-093-pre).

Fake-clock only: injected ``sleep_fn`` + a fake client. No network. Mirrors
the memory-scheduler fake-sleep pattern (``tests/memory/test_tidy_scheduler``).
"""

from __future__ import annotations

import asyncio

import pytest

from trowel_py.quota.read_model import QuotaReadModel
from trowel_py.quota.scheduler import GlmAccount, QuotaScheduler
from trowel_py.quota.types import (
    Provider,
    QuotaSnapshot,
    QuotaStatus,
    QuotaWindow,
    QuotaWindowKind,
)


def _ok(account_id: str) -> QuotaSnapshot:
    return QuotaSnapshot(
        provider=Provider.GLM,
        account_id=account_id,
        plan_level="max",
        windows=(
            QuotaWindow(
                kind=QuotaWindowKind.WEEKLY,
                used_percent=10,
                resets_at=None,
                raw={},
            ),
        ),
        fetched_at=0,
        status=QuotaStatus.OK,
    )


class _FakeClient:
    """Records fetches; returns a snapshot per account, or raises on cue."""

    def __init__(self, *, fail_on: str | None = None) -> None:
        self.fetches: list[str] = []
        self._fail_on = fail_on

    async def fetch(self, account_id: str, api_key: str) -> QuotaSnapshot:
        self.fetches.append(account_id)
        if account_id == self._fail_on:
            raise RuntimeError("boom")
        return _ok(account_id)


class _BudgetSleep:
    """Records durations; returns for the first ``budget`` calls then hangs."""

    def __init__(self, budget: int) -> None:
        self._budget = budget
        self.durations: list[float] = []

    async def __call__(self, seconds: float) -> None:
        self.durations.append(seconds)
        if len(self.durations) > self._budget:
            await asyncio.Event().wait()  # hang forever


def _hang_on_big(threshold: float):
    async def sleep(seconds: float) -> None:
        if seconds >= threshold:
            await asyncio.Event().wait()

    return sleep


def _accounts(*ids: str) -> tuple[GlmAccount, ...]:
    return tuple(GlmAccount(account_id=i, api_key=f"k-{i}") for i in ids)


async def test_start_is_idempotent() -> None:
    sched = QuotaScheduler(
        _accounts("a"),
        _FakeClient(),
        QuotaReadModel(now_ms=lambda: 0),
        sleep_fn=_hang_on_big(300),
    )
    await sched.start()
    first = sched.tasks
    await sched.start()  # no-op
    assert sched.tasks == first
    await sched.stop()
    assert sched.tasks == ()


async def test_first_cycle_polls_every_account_and_feeds_read_model() -> None:
    """First cycle runs immediately (stagger returns, interval hangs)."""

    client = _FakeClient()
    rm = QuotaReadModel(now_ms=lambda: 0, stale_after_ms=10**9)
    sched = QuotaScheduler(
        _accounts("a", "b"),
        client,
        rm,
        interval_s=300.0,
        stagger_s=0.5,
        sleep_fn=_hang_on_big(300.0),
    )
    await sched.start()
    await asyncio.sleep(0.02)  # let the first cycle run

    assert client.fetches == ["a", "b"]
    assert rm.get(Provider.GLM, "a") is not None
    assert rm.get(Provider.GLM, "b") is not None
    await sched.stop()


async def test_loop_repeats_at_interval_with_stagger_between_accounts() -> None:
    """Two cycles: polls repeat [a,b,a,b]; recorded sleeps include both the
    inter-account stagger and the inter-cycle interval."""

    sleep = _BudgetSleep(budget=3)  # 3 sleeps return; 4th hangs
    client = _FakeClient()
    sched = QuotaScheduler(
        _accounts("a", "b"),
        client,
        QuotaReadModel(now_ms=lambda: 0),
        interval_s=300.0,
        stagger_s=0.5,
        sleep_fn=sleep,
    )
    await sched.start()
    await asyncio.sleep(0.02)

    assert client.fetches == ["a", "b", "a", "b"]  # two full cycles
    # stagger (0.5) sits between accounts, interval (300) between cycles
    assert 0.5 in sleep.durations
    assert 300.0 in sleep.durations
    await sched.stop()


async def test_single_account_has_no_stagger_sleep() -> None:
    """One account -> no inter-account stagger, only the interval pacing."""

    sleep = _BudgetSleep(budget=0)  # first sleep (interval) hangs
    client = _FakeClient()
    sched = QuotaScheduler(
        _accounts("solo"),
        client,
        QuotaReadModel(now_ms=lambda: 0),
        interval_s=300.0,
        stagger_s=0.5,
        sleep_fn=sleep,
    )
    await sched.start()
    await asyncio.sleep(0.02)

    assert client.fetches == ["solo"]
    assert sleep.durations == [300.0]  # only the interval, no stagger
    await sched.stop()


async def test_poll_failure_does_not_kill_loop() -> None:
    """An account whose fetch raises is skipped; the loop survives and the
    healthy account keeps being polled."""

    sleep = _BudgetSleep(budget=3)
    client = _FakeClient(fail_on="b")
    rm = QuotaReadModel(now_ms=lambda: 0, stale_after_ms=10**9)
    sched = QuotaScheduler(
        _accounts("a", "b"),
        client,
        rm,
        interval_s=300.0,
        stagger_s=0.5,
        sleep_fn=sleep,
    )
    await sched.start()
    await asyncio.sleep(0.02)

    # "a" polled twice (two cycles); "b" raised both times but loop survived
    assert client.fetches == ["a", "b", "a", "b"]
    assert rm.get(Provider.GLM, "a") is not None  # healthy account recorded
    assert rm.get(Provider.GLM, "b") is None  # failing account never recorded
    await sched.stop()


async def test_zero_accounts_does_nothing() -> None:
    """No accounts -> start/stop cleanly, no fetches."""

    client = _FakeClient()
    sleep = _BudgetSleep(budget=0)
    sched = QuotaScheduler(
        (), client, QuotaReadModel(now_ms=lambda: 0), sleep_fn=sleep
    )
    await sched.start()
    await asyncio.sleep(0.01)
    assert client.fetches == []
    await sched.stop()


@pytest.mark.filterwarnings("ignore::DeprecationWarning")
async def test_stop_cancels_running_loop() -> None:
    """stop() cancels a loop hung on the interval sleep."""

    sched = QuotaScheduler(
        _accounts("a"),
        _FakeClient(),
        QuotaReadModel(now_ms=lambda: 0),
        sleep_fn=_hang_on_big(300.0),
    )
    await sched.start()
    await asyncio.sleep(0.01)
    await sched.stop()  # must return, not hang
    assert sched.tasks == ()
