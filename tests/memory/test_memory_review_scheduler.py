"""tests for the in-app memory review scheduler (slice-046).

Replaces the launchd path (slice-040-b ``schedule.py``): the scheduler lives
inside the trowel app process and fires (a) a startup catchup + (b) a daily
fixed-time run. No real cc is spawned — ``dispatch_fn`` is injected (#46416).
Clocks are injected (``now_fn`` / ``sleep_fn``) so tests never sleep on the
wall clock.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, time

import pytest

from trowel_py.memory.review_scheduler import (
    DEFAULT_REVIEW_ENABLED,
    DEFAULT_REVIEW_TIME,
    MemoryReviewScheduler,
    ReviewScheduleConfig,
    load_review_config,
    seconds_until,
)


@pytest.fixture(autouse=True)
def _reset_register_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    """reset the module-level register guard between tests so a prior
    _default_dispatch call doesn't leak register state forward (slice-046 CR)."""
    import trowel_py.memory.review_scheduler as rs

    monkeypatch.setattr(rs, "_REVIEW_JOB_REGISTERED", False)


# ---------- T1: pure time math + config ----------


class TestSecondsUntil:
    def test_same_day_when_target_ahead(self):
        # 01:00 -> 02:30 same day = 1h30m = 5400s
        assert seconds_until(time(2, 30), datetime(2026, 7, 13, 1, 0)) == pytest.approx(5400)

    def test_cross_midnight_when_target_passed(self):
        # 03:00 -> 02:30 tomorrow = 23h30m = 84600s
        assert seconds_until(time(2, 30), datetime(2026, 7, 13, 3, 0)) == pytest.approx(84600)

    def test_exact_now_rolls_to_tomorrow(self):
        # 02:30:00 -> next 02:30 is tomorrow (delta<=0 -> +24h)
        assert seconds_until(time(2, 30), datetime(2026, 7, 13, 2, 30, 0)) == pytest.approx(86400)


class TestLoadReviewConfig:
    def test_defaults_when_no_memory_section(self, tmp_path):
        cfg = tmp_path / "config.toml"
        cfg.write_text('[llm]\nactive = "x"\n[llm.x]\nbase_url = "u"\n')
        c = load_review_config(cfg)
        assert c.review_time == DEFAULT_REVIEW_TIME
        assert c.review_enabled is DEFAULT_REVIEW_ENABLED

    def test_override_time_and_enabled(self, tmp_path):
        cfg = tmp_path / "config.toml"
        cfg.write_text('[memory]\nreview_time = "03:15"\nreview_enabled = false\n')
        c = load_review_config(cfg)
        assert c.review_time == time(3, 15)
        assert c.review_enabled is False

    def test_invalid_time_falls_back(self, tmp_path):
        cfg = tmp_path / "config.toml"
        cfg.write_text('[memory]\nreview_time = "not-a-time"\n')
        c = load_review_config(cfg)
        assert c.review_time == DEFAULT_REVIEW_TIME  # C-7 fallback, no raise

    def test_missing_config_file_uses_defaults(self, tmp_path):
        c = load_review_config(tmp_path / "does-not-exist.toml")
        assert c.review_time == DEFAULT_REVIEW_TIME
        assert c.review_enabled is True


# ---------- helpers for T2 ----------


def _cfg(*, enabled: bool = True, t: time | None = None) -> ReviewScheduleConfig:
    return ReviewScheduleConfig(review_time=t or time(2, 30), review_enabled=enabled)


class _HangingSleep:
    """sleep_fn that hangs forever — keeps the daily loop idle so catchup is
    the only thing that fires in a start() test."""

    async def __call__(self, seconds: float) -> None:  # noqa: ARG002
        await asyncio.Event().wait()


class _BudgetSleep:
    """sleep_fn that returns immediately for the first ``budget`` calls, then
    raises CancelledError so the daily loop stops after ``budget`` runs (no
    busy-looping, no real waiting). Records every requested wait.

    CancelledError is reused as the stop sentinel (rather than a custom
    exception) because ``_daily_loop`` already handles it as its cancel path —
    no test needs to teach the loop a new exception type."""

    def __init__(self, budget: int) -> None:
        self.budget = budget
        self.waits: list[float] = []

    async def __call__(self, seconds: float) -> None:
        self.waits.append(seconds)
        if len(self.waits) > self.budget:
            raise asyncio.CancelledError


async def _wait_until(predicate, *, timeout: float = 1.0) -> bool:
    """Poll ``predicate`` until true or ``timeout`` — replaces a fixed
    ``asyncio.sleep`` in tests that wait on a worker-thread dispatch landing
    (a fixed wait is flaky on slow CI; this bounds the wait without racing)."""
    elapsed = 0.0
    while elapsed < timeout:
        if predicate():
            return True
        await asyncio.sleep(0.01)
        elapsed += 0.01
    return False


# ---------- T2: scheduler behavior ----------


class TestSchedulerRunOnce:
    async def test_run_once_dispatches_with_root(self, tmp_path):
        calls: list[dict] = []
        sched = MemoryReviewScheduler(_cfg(), tmp_path, dispatch_fn=calls.append)
        await sched._run_once()
        assert len(calls) == 1
        assert calls[0]["root"] == str(tmp_path)
        assert "date" in calls[0]

    async def test_run_once_swallows_dispatch_exception(self, tmp_path):
        def boom(_event: dict) -> None:
            raise RuntimeError("cc exploded")

        sched = MemoryReviewScheduler(_cfg(), tmp_path, dispatch_fn=boom)
        # C-5: must not raise out of the scheduler
        await sched._run_once()


class TestSchedulerStartStop:
    async def test_disabled_creates_no_tasks(self, tmp_path):
        sched = MemoryReviewScheduler(
            _cfg(enabled=False), tmp_path, dispatch_fn=lambda _e: None, sleep_fn=_HangingSleep()
        )
        await sched.start()
        assert sched.tasks == ()  # C-6: enabled=false -> nothing scheduled
        await sched.stop()

    async def test_start_creates_two_tasks(self, tmp_path):
        sched = MemoryReviewScheduler(
            _cfg(), tmp_path, dispatch_fn=lambda _e: None, sleep_fn=_HangingSleep()
        )
        await sched.start()
        assert len(sched.tasks) == 2  # catchup + daily loop
        await sched.stop()
        assert sched.tasks == ()

    async def test_start_is_idempotent(self, tmp_path):
        sched = MemoryReviewScheduler(
            _cfg(), tmp_path, dispatch_fn=lambda _e: None, sleep_fn=_HangingSleep()
        )
        await sched.start()
        n = len(sched.tasks)
        await sched.start()  # second start is a no-op
        assert len(sched.tasks) == n
        await sched.stop()

    async def test_catchup_fires_once_on_start(self, tmp_path):
        calls: list[dict] = []
        sched = MemoryReviewScheduler(
            _cfg(), tmp_path, dispatch_fn=calls.append, sleep_fn=_HangingSleep()
        )
        await sched.start()
        fired = await _wait_until(lambda: len(calls) >= 1)  # catchup dispatches from a worker thread
        await sched.stop()
        assert fired
        assert len(calls) == 1  # only catchup; daily loop hung on first sleep
        assert calls[0]["root"] == str(tmp_path)


class TestSchedulerDailyLoop:
    async def test_loop_dispatches_each_interval(self, tmp_path):
        calls: list[dict] = []
        sleep = _BudgetSleep(budget=2)
        sched = MemoryReviewScheduler(
            _cfg(t=time(2, 30)),
            tmp_path,
            dispatch_fn=calls.append,
            now_fn=lambda: datetime(2026, 7, 13, 1, 0),  # fixed -> 5400s each
            sleep_fn=sleep,
        )
        await sched._daily_loop()  # returns when sleep raises CancelledError
        assert len(calls) == 2
        assert all(w == pytest.approx(5400) for w in sleep.waits[:2])

    async def test_loop_uses_configured_time(self, tmp_path):
        calls: list[dict] = []
        sleep = _BudgetSleep(budget=1)
        sched = MemoryReviewScheduler(
            _cfg(t=time(3, 15)),
            tmp_path,
            dispatch_fn=calls.append,
            now_fn=lambda: datetime(2026, 7, 13, 3, 0),  # 03:00 -> 03:15 = 900s
            sleep_fn=sleep,
        )
        await sched._daily_loop()
        assert len(calls) == 1
        assert sleep.waits[0] == pytest.approx(900)


# ---------- T3: lifespan integration ----------


class TestLifespanIntegration:
    """verify app.py lifespan wires the scheduler in (startup) and out
    (shutdown). Uses TestClient so the real lifespan runs;
    resolve_memory_root + dispatch are monkeypatched so no real ~/.trowel
    write or cc spawn (#46416) happens during the test.
    """

    def test_startup_starts_scheduler(self, tmp_path, monkeypatch):
        from fastapi.testclient import TestClient

        from trowel_py.app import create_app
        from trowel_py.memory import paths as mem_paths
        from trowel_py.memory import review_scheduler as rs_mod

        monkeypatch.setattr(mem_paths, "resolve_memory_root", lambda: tmp_path)
        monkeypatch.setattr(rs_mod, "_default_dispatch", lambda _e: None)
        app = create_app()
        with TestClient(app):
            sched = app.state.memory_scheduler
            assert sched is not None
            assert sched._started is True
            assert len(sched.tasks) == 2  # catchup + daily loop
        # shutdown stopped it
        assert app.state.memory_scheduler.tasks == ()

    def test_disabled_does_not_start(self, tmp_path, monkeypatch):
        from fastapi.testclient import TestClient

        from trowel_py.app import create_app
        from trowel_py.memory import paths as mem_paths
        from trowel_py.memory import review_scheduler as rs_mod

        monkeypatch.setattr(mem_paths, "resolve_memory_root", lambda: tmp_path)
        monkeypatch.setattr(
            rs_mod,
            "load_review_config",
            lambda *_a, **_k: rs_mod.ReviewScheduleConfig(rs_mod.DEFAULT_REVIEW_TIME, False),
        )
        app = create_app()
        with TestClient(app):
            sched = app.state.memory_scheduler
            assert sched is not None
            assert sched._started is False
            assert sched.tasks == ()
