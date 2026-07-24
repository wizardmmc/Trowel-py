"""Scheduler 的定时循环、启停和 app lifespan 集成。"""

from __future__ import annotations

import asyncio
from datetime import datetime, time
from pathlib import Path

from trowel_py.memory.tidy_scheduler import (
    DEFAULT_MONTHLY_TIME,
    DEFAULT_WEEKLY_TIME,
    TidyScheduler,
)
from trowel_py.memory.tidy_state import TidyState, load_state, save_state

from .support import (
    BudgetSleep,
    HangingSleep,
    noop_provider,
    now_w31,
    ok_report,
    recording_success,
)


class TestLoops:
    async def test_weekly_loop_runs_catchup_on_wake(self, tmp_path: Path):
        sleep = BudgetSleep(budget=1)
        scheduler = TidyScheduler(
            tmp_path,
            noop_provider,
            now_fn=lambda: datetime(2026, 7, 12, 1, 0),
            sleep_fn=sleep,
            weekly_fn=ok_report,
            monthly_fn=ok_report,
        )
        woke: list[str] = []

        async def fake_run(scope):
            woke.append(scope)

        scheduler._run_catchup_scope = fake_run  # type: ignore[method-assign]
        await scheduler._weekly_loop()
        assert woke == ["weekly"]

    async def test_monthly_loop_skips_when_weekly_behind(self, tmp_path: Path):
        save_state(tmp_path, TidyState(weekly_last="2026-W20"))
        sleep = BudgetSleep(budget=1)
        scheduler = TidyScheduler(
            tmp_path,
            noop_provider,
            now_fn=now_w31,
            sleep_fn=sleep,
            weekly_fn=ok_report,
            monthly_fn=ok_report,
        )
        ran: list[str] = []

        async def fake_run(scope):
            ran.append(scope)

        scheduler._run_catchup_scope = fake_run  # type: ignore[method-assign]
        await scheduler._monthly_loop()
        assert ran == []

    async def test_monthly_loop_runs_when_weekly_clear(self, tmp_path: Path):
        save_state(tmp_path, TidyState(weekly_last="2026-W30"))
        sleep = BudgetSleep(budget=1)
        scheduler = TidyScheduler(
            tmp_path,
            noop_provider,
            now_fn=now_w31,
            sleep_fn=sleep,
            weekly_fn=ok_report,
            monthly_fn=ok_report,
        )
        ran: list[str] = []

        async def fake_run(scope):
            ran.append(scope)

        scheduler._run_catchup_scope = fake_run  # type: ignore[method-assign]
        await scheduler._monthly_loop()
        assert ran == ["monthly"]


class TestStartStop:
    async def test_start_runs_startup_catchup_in_thread(self, tmp_path: Path):
        calls: list[str] = []
        save_state(tmp_path, TidyState(weekly_last="2026-W29"))
        scheduler = TidyScheduler(
            tmp_path,
            noop_provider,
            now_fn=now_w31,
            sleep_fn=HangingSleep(),
            weekly_fn=recording_success(calls),
            monthly_fn=ok_report,
        )
        await scheduler.start()
        assert scheduler._started is True
        for _ in range(200):
            if calls:
                break
            await asyncio.sleep(0.005)
        assert calls == ["2026-W30"]
        assert load_state(tmp_path).weekly_last == "2026-W30"
        await scheduler.stop()
        assert scheduler.tasks == ()

    async def test_start_is_idempotent(self, tmp_path: Path):
        scheduler = TidyScheduler(
            tmp_path,
            noop_provider,
            now_fn=now_w31,
            sleep_fn=HangingSleep(),
            weekly_fn=ok_report,
            monthly_fn=ok_report,
        )
        await scheduler.start()
        task_count = len(scheduler.tasks)
        await scheduler.start()
        assert len(scheduler.tasks) == task_count
        await scheduler.stop()

    async def test_stop_does_not_block_on_long_backlog(self, tmp_path: Path):
        calls: list[str] = []

        def run(period: str) -> dict:
            calls.append(period)
            return ok_report(period)

        save_state(tmp_path, TidyState(weekly_last="2026-W27"))
        scheduler = TidyScheduler(
            tmp_path,
            noop_provider,
            now_fn=now_w31,
            sleep_fn=HangingSleep(),
            weekly_fn=run,
            monthly_fn=ok_report,
        )
        await scheduler.start()
        await asyncio.wait_for(scheduler.stop(), timeout=5.0)
        assert scheduler.tasks == ()

    def test_default_times_off_phase(self):
        assert DEFAULT_WEEKLY_TIME == time(3, 30)
        assert DEFAULT_MONTHLY_TIME == time(4, 0)

    async def test_stop_keeps_stopping_true_for_lingering_thread(
        self,
        tmp_path: Path,
    ):
        scheduler = TidyScheduler(
            tmp_path,
            noop_provider,
            now_fn=now_w31,
            sleep_fn=HangingSleep(),
            weekly_fn=ok_report,
            monthly_fn=ok_report,
        )
        await scheduler.start()
        await scheduler.stop()
        assert scheduler._stopping is True
        await scheduler.start()
        assert scheduler._stopping is False
        await scheduler.stop()


class TestLifespanIntegration:
    def test_startup_starts_tidy_scheduler(self, tmp_path: Path, monkeypatch):
        from fastapi.testclient import TestClient

        from trowel_py.app import create_app
        from trowel_py.memory import paths as memory_paths
        from trowel_py.memory import tidy as tidy_module
        from trowel_py.memory.daily_review import scheduler as review_scheduler
        from trowel_py.memory.profile_distill import scheduler as distill_scheduler

        monkeypatch.setattr(memory_paths, "resolve_memory_root", lambda: tmp_path)
        monkeypatch.setattr(review_scheduler, "_default_dispatch", lambda _event: None)
        monkeypatch.setattr(distill_scheduler, "_default_dispatch", lambda _event: None)
        monkeypatch.setattr(
            tidy_module,
            "run_weekly_tidy",
            lambda root, week, provider: ok_report(week),
        )
        monkeypatch.setattr(
            tidy_module,
            "run_monthly_tidy",
            lambda root, month, provider, **kwargs: ok_report(month),
        )
        app = create_app()
        with TestClient(app):
            scheduler = app.state.tidy_scheduler
            assert scheduler is not None
            assert scheduler._started is True
            assert len(scheduler.tasks) == 3
        assert app.state.tidy_scheduler.tasks == ()
