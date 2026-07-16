"""tests for the in-app weekly/monthly tidy scheduler (slice-052).

Tidy does NOT spawn cc — Python calls the provider directly (C-4), so the
scheduler is simpler than review/distill. Clocks + tidy functions are injected
(``now_fn`` / ``sleep_fn`` / ``weekly_fn`` / ``monthly_fn``): no wall-clock
sleep, no real provider, no cc subprocess (#46416). Fires on COMPLETED
intervals (C-3): weekly → last ISO week, monthly → last month.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, time
from pathlib import Path

import pytest

from trowel_py.memory.tidy_scheduler import (
    DEFAULT_MONTHLY_TIME,
    DEFAULT_WEEKLY_TIME,
    TidyScheduler,
    last_iso_week,
    last_month,
    seconds_until_next_monthday,
    seconds_until_next_weekday,
)


# ---------- helpers (mirror test_memory_review_scheduler) ----------


class _HangingSleep:
    """sleep_fn that hangs forever — keeps loops idle so start() tests don't
    fire any tidy."""

    async def __call__(self, seconds: float) -> None:  # noqa: ARG002
        await asyncio.Event().wait()


class _BudgetSleep:
    """sleep_fn that returns for the first ``budget`` calls then raises
    CancelledError so the loop stops after ``budget`` tidy runs. Records waits."""

    def __init__(self, budget: int) -> None:
        self.budget = budget
        self.waits: list[float] = []

    async def __call__(self, seconds: float) -> None:
        self.waits.append(seconds)
        if len(self.waits) > self.budget:
            raise asyncio.CancelledError


def _noop_provider() -> None:
    """provider_factory stub — never called when weekly_fn/monthly_fn injected."""
    return None


# ---------- T1: pure time math ----------


class TestLastIsoWeek:
    def test_from_monday_trigger_is_previous_week(self):
        # Mon 2026-07-13 (W29) 03:30 fires → tidy last week W28
        assert last_iso_week(datetime(2026, 7, 13, 3, 30)) == "2026-W28"

    def test_midweek_still_tidy_last_week(self):
        # Wed 2026-07-15 (W29, in-progress) → still W28 (C-3: never this week)
        assert last_iso_week(datetime(2026, 7, 15, 3, 30)) == "2026-W28"

    def test_cross_year(self):
        # Mon 2027-01-04 (W1) → last week is 2026-W53 (跨年)
        assert last_iso_week(datetime(2027, 1, 4, 3, 30)) == "2026-W53"

    def test_from_sunday_evening(self):
        # Sun 2026-07-12 23:59 is the last day of W28 (still in-progress until
        # Mon 00:00), so the last COMPLETED week is W27 (C-3: never this week).
        # now-7d = 2026-07-05 (Sun, end of W27) → "2026-W27".
        assert last_iso_week(datetime(2026, 7, 12, 23, 59)) == "2026-W27"


class TestLastMonth:
    def test_from_first_of_month(self):
        assert last_month(datetime(2027, 1, 1, 4, 0)) == "2026-12"

    def test_mid_month(self):
        assert last_month(datetime(2026, 3, 15, 4, 0)) == "2026-02"

    def test_july_first(self):
        assert last_month(datetime(2026, 7, 1, 4, 0)) == "2026-06"


class TestSecondsUntilNextWeekday:
    def test_sunday_to_monday(self):
        # Sun 2026-07-12 01:00 → Mon 03:30 = 26h30m = 95400s
        now = datetime(2026, 7, 12, 1, 0)
        assert now.weekday() == 6  # Sunday guard
        assert seconds_until_next_weekday(now, 0, time(3, 30)) == pytest.approx(95400)

    def test_same_day_when_target_ahead(self):
        # Mon 03:00 → Mon 03:30 = 1800s
        assert seconds_until_next_weekday(
            datetime(2026, 7, 13, 3, 0), 0, time(3, 30)
        ) == pytest.approx(1800)

    def test_rolls_to_next_week_when_passed(self):
        # Mon 04:00 → next Mon 03:30 = 603000s
        assert seconds_until_next_weekday(
            datetime(2026, 7, 13, 4, 0), 0, time(3, 30)
        ) == pytest.approx(603000)


class TestSecondsUntilNextMonthday:
    def test_mid_month_to_next_first(self):
        # 2026-07-15 04:00 → 2026-08-01 04:00 = 1468800s
        assert seconds_until_next_monthday(
            datetime(2026, 7, 15, 4, 0), 1, time(4, 0)
        ) == pytest.approx(1468800)

    def test_same_day_when_target_ahead(self):
        # 2026-07-01 03:00 → 2026-07-01 04:00 = 3600s
        assert seconds_until_next_monthday(
            datetime(2026, 7, 1, 3, 0), 1, time(4, 0)
        ) == pytest.approx(3600)

    def test_rolls_to_next_month_when_passed(self):
        # 2026-07-01 05:00 → 2026-08-01 04:00 = 2674800s
        assert seconds_until_next_monthday(
            datetime(2026, 7, 1, 5, 0), 1, time(4, 0)
        ) == pytest.approx(2674800)


# ---------- T2: scheduler loops ----------


class TestWeeklyLoop:
    async def test_fires_last_iso_week_after_wait(self, tmp_path: Path):
        calls: list[str] = []
        sleep = _BudgetSleep(budget=1)
        sched = TidyScheduler(
            tmp_path, _noop_provider,
            now_fn=lambda: datetime(2026, 7, 12, 1, 0),  # Sun 01:00
            sleep_fn=sleep,
            weekly_fn=calls.append,
            monthly_fn=lambda _m: None,
        )
        await sched._weekly_loop()
        assert sleep.waits[0] == pytest.approx(95400)  # Sun 01:00 → Mon 03:30
        assert calls == ["2026-W28"]  # fired on the completed week

    async def test_swallows_tidy_failure(self, tmp_path: Path):
        def boom(iso_week: str) -> None:
            raise RuntimeError("tidy exploded")

        sleep = _BudgetSleep(budget=1)
        sched = TidyScheduler(
            tmp_path, _noop_provider,
            now_fn=lambda: datetime(2026, 7, 12, 1, 0),
            sleep_fn=sleep,
            weekly_fn=boom,
            monthly_fn=lambda _m: None,
        )
        await sched._weekly_loop()  # C-5: must not raise


class TestMonthlyLoop:
    async def test_fires_last_month_after_wait(self, tmp_path: Path):
        calls: list[str] = []
        sleep = _BudgetSleep(budget=1)
        sched = TidyScheduler(
            tmp_path, _noop_provider,
            now_fn=lambda: datetime(2026, 7, 15, 4, 0),
            sleep_fn=sleep,
            weekly_fn=lambda _w: None,
            monthly_fn=calls.append,
        )
        await sched._monthly_loop()
        assert sleep.waits[0] == pytest.approx(1468800)  # Jul 15 → Aug 01
        assert calls == ["2026-07"]  # last completed month

    async def test_swallows_tidy_failure(self, tmp_path: Path):
        def boom(month: str) -> None:
            raise RuntimeError("tidy exploded")

        sleep = _BudgetSleep(budget=1)
        sched = TidyScheduler(
            tmp_path, _noop_provider,
            now_fn=lambda: datetime(2026, 7, 15, 4, 0),
            sleep_fn=sleep,
            weekly_fn=lambda _w: None,
            monthly_fn=boom,
        )
        await sched._monthly_loop()  # C-5: must not raise


class TestStartStop:
    async def test_start_creates_two_tasks(self, tmp_path: Path):
        sched = TidyScheduler(
            tmp_path, _noop_provider,
            now_fn=lambda: datetime(2026, 7, 12, 1, 0),
            sleep_fn=_HangingSleep(),
            weekly_fn=lambda _w: None,
            monthly_fn=lambda _m: None,
        )
        await sched.start()
        assert len(sched.tasks) == 2  # weekly + monthly loop
        await sched.stop()
        assert sched.tasks == ()

    async def test_start_is_idempotent(self, tmp_path: Path):
        sched = TidyScheduler(
            tmp_path, _noop_provider,
            now_fn=lambda: datetime(2026, 7, 12, 1, 0),
            sleep_fn=_HangingSleep(),
            weekly_fn=lambda _w: None,
            monthly_fn=lambda _m: None,
        )
        await sched.start()
        n = len(sched.tasks)
        await sched.start()  # no-op
        assert len(sched.tasks) == n
        await sched.stop()

    def test_default_times_off_phase(self):
        # weekly Mon 03:30, monthly 1st 04:00 — off-phase from review 02:30
        # / distill 02:50 (boundary: tidy slots after them).
        assert DEFAULT_WEEKLY_TIME == time(3, 30)
        assert DEFAULT_MONTHLY_TIME == time(4, 0)


# ---------- T3: lifespan integration ----------


class TestLifespanIntegration:
    """verify app.py lifespan wires the tidy scheduler in (startup) and out
    (shutdown). TestClient runs the real lifespan; resolve_memory_root + the
    sibling schedulers' dispatches are monkeypatched so no real ~/.trowel
    write or cc spawn happens (#46416). Tidy itself only sleeps (no catchup),
    so it needs no dispatch patch."""

    def test_startup_starts_tidy_scheduler(self, tmp_path: Path, monkeypatch):
        from fastapi.testclient import TestClient

        from trowel_py.app import create_app
        from trowel_py.memory import paths as mem_paths
        from trowel_py.memory import profile_distill_scheduler as ds_mod
        from trowel_py.memory import review_scheduler as rs_mod

        monkeypatch.setattr(mem_paths, "resolve_memory_root", lambda: tmp_path)
        # keep the sibling schedulers' startup catchups from doing real work
        monkeypatch.setattr(rs_mod, "_default_dispatch", lambda _e: None)
        monkeypatch.setattr(ds_mod, "_default_dispatch", lambda _e: None)
        app = create_app()
        with TestClient(app):
            sched = app.state.tidy_scheduler
            assert sched is not None
            assert sched._started is True
            assert len(sched.tasks) == 2  # weekly + monthly loop
        # shutdown stopped it
        assert app.state.tidy_scheduler.tasks == ()
