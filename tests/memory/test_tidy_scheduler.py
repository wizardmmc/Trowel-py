"""tests for the in-app weekly/monthly tidy scheduler (slice-052, slice-063).

slice-063 adds the success watermark + startup catchup. Clocks + tidy fns are
injected (``now_fn`` / ``sleep_fn`` / ``weekly_fn`` / ``monthly_fn``): no
wall-clock sleep, no real provider, no cc subprocess (#46416). Fires on
COMPLETED intervals (C-2): weekly → pending completed ISO weeks, monthly →
pending completed months, oldest-first; a non-success stops the scope (C-4),
monthly waits for weekly (C-5).
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
    tidy_succeeded,
)
from trowel_py.memory.tidy_state import TidyState, load_state, save_state


class _HangingSleep:
    """sleep_fn that hangs forever — keeps loops idle so start() tests don't
    fire any scheduled wake (only the startup catchup runs)."""

    async def __call__(self, seconds: float) -> None:  # noqa: ARG002
        await asyncio.Event().wait()


class _BudgetSleep:
    """sleep_fn that returns for the first ``budget`` calls then raises
    CancelledError so the loop stops after ``budget`` wakes. Records waits."""

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


def _ok(_period: str) -> dict:
    """a fully-successful tidy report (clean no-op, operations == 0)."""
    return {"plan_id": "p", "compress": {}, "tidy": {"plan_id": "p", "applied": [], "operations": 0}}


def _err(_period: str) -> dict:
    """a tidy report whose apply failed (stale/invalid)."""
    return {"plan_id": "p", "tidy": {"plan_id": "p", "error": "stale", "applied": [], "operations": 1}}


def _skipped(_period: str) -> dict:
    """a tidy report that lost the lock to another tidy run."""
    return {"plan_id": "p", "skipped": "another tidy is running"}


#: Mon 2026-07-27 is W31 → last_iso_week = W30. Watermark W27 → 3-week gap.
def _NOW_W31() -> datetime:
    return datetime(2026, 7, 27, 8, 0)


# ---------- T1: pure time math ----------


class TestLastIsoWeek:
    def test_from_monday_trigger_is_previous_week(self):
        assert last_iso_week(datetime(2026, 7, 13, 3, 30)) == "2026-W28"

    def test_midweek_still_tidy_last_week(self):
        assert last_iso_week(datetime(2026, 7, 15, 3, 30)) == "2026-W28"

    def test_cross_year(self):
        assert last_iso_week(datetime(2027, 1, 4, 3, 30)) == "2026-W53"

    def test_from_sunday_evening(self):
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
        now = datetime(2026, 7, 12, 1, 0)
        assert now.weekday() == 6
        assert seconds_until_next_weekday(now, 0, time(3, 30)) == pytest.approx(95400)

    def test_same_day_when_target_ahead(self):
        assert seconds_until_next_weekday(
            datetime(2026, 7, 13, 3, 0), 0, time(3, 30)
        ) == pytest.approx(1800)

    def test_rolls_to_next_week_when_passed(self):
        assert seconds_until_next_weekday(
            datetime(2026, 7, 13, 4, 0), 0, time(3, 30)
        ) == pytest.approx(603000)


class TestSecondsUntilNextMonthday:
    def test_mid_month_to_next_first(self):
        assert seconds_until_next_monthday(
            datetime(2026, 7, 15, 4, 0), 1, time(4, 0)
        ) == pytest.approx(1468800)

    def test_same_day_when_target_ahead(self):
        assert seconds_until_next_monthday(
            datetime(2026, 7, 1, 3, 0), 1, time(4, 0)
        ) == pytest.approx(3600)

    def test_rolls_to_next_month_when_passed(self):
        assert seconds_until_next_monthday(
            datetime(2026, 7, 1, 5, 0), 1, time(4, 0)
        ) == pytest.approx(2674800)


# ---------- T2: tidy_succeeded gate ----------


class TestTidySucceeded:
    def test_clean_noop_is_success(self):
        assert tidy_succeeded(_ok("2026-W28")) is True

    def test_success_with_operations(self):
        assert tidy_succeeded({"tidy": {"applied": ["a"], "operations": 1}}) is True

    def test_skipped_is_not_success(self):
        assert tidy_succeeded(_skipped("2026-W28")) is False

    def test_apply_error_is_not_success(self):
        assert tidy_succeeded(_err("2026-W28")) is False

    def test_non_dict_is_not_success(self):
        assert tidy_succeeded(None) is False
        assert tidy_succeeded("ok") is False

    def test_no_failure_signal_is_success(self):
        # a report with neither skipped nor tidy.error is a success
        assert tidy_succeeded({"plan_id": "p"}) is True


# ---------- T3: catchup scope (the slice-063 core) ----------


class TestCatchupScope:
    """C-1/C-3/C-4: enumerate pending oldest-first, advance only on success,
    stop the scope at the first non-success so the next run retries it."""

    def _sched(self, tmp_path: Path, *, weekly_fn, monthly_fn=_ok) -> TidyScheduler:
        return TidyScheduler(
            tmp_path, _noop_provider,
            now_fn=_NOW_W31, sleep_fn=_HangingSleep(),
            weekly_fn=weekly_fn, monthly_fn=monthly_fn,
        )

    def test_three_week_gap_advances_one_at_a_time(self, tmp_path: Path):
        calls: list[str] = []
        save_state(tmp_path, TidyState(weekly_last="2026-W27"))
        sched = self._sched(tmp_path, weekly_fn=lambda p: (calls.append(p), _ok(p))[1])
        sched._catchup_scope_sync("weekly", datetime(2026, 7, 27, 8, 0))
        assert calls == ["2026-W28", "2026-W29", "2026-W30"]
        assert load_state(tmp_path).weekly_last == "2026-W30"

    def test_failure_stops_scope_and_keeps_watermark(self, tmp_path: Path):
        calls: list[str] = []

        def fn(period: str) -> dict:
            calls.append(period)
            return _err(period) if period == "2026-W29" else _ok(period)

        save_state(tmp_path, TidyState(weekly_last="2026-W27"))
        sched = self._sched(tmp_path, weekly_fn=fn)
        sched._catchup_scope_sync("weekly", datetime(2026, 7, 27, 8, 0))
        # W28 succeeded (advanced), W29 failed (stopped) → W30 never run
        assert calls == ["2026-W28", "2026-W29"]
        assert load_state(tmp_path).weekly_last == "2026-W28"

    def test_exception_stops_scope_and_keeps_watermark(self, tmp_path: Path):
        calls: list[str] = []

        def fn(period: str) -> dict:
            calls.append(period)
            if period == "2026-W29":
                raise RuntimeError("provider exploded")
            return _ok(period)

        save_state(tmp_path, TidyState(weekly_last="2026-W27"))
        sched = self._sched(tmp_path, weekly_fn=fn)
        sched._catchup_scope_sync("weekly", datetime(2026, 7, 27, 8, 0))
        assert calls == ["2026-W28", "2026-W29"]
        assert load_state(tmp_path).weekly_last == "2026-W28"

    def test_skipped_lock_does_not_advance(self, tmp_path: Path):
        save_state(tmp_path, TidyState(weekly_last="2026-W29"))  # only W30 pending
        sched = self._sched(tmp_path, weekly_fn=_skipped)
        sched._catchup_scope_sync("weekly", datetime(2026, 7, 27, 8, 0))
        assert load_state(tmp_path).weekly_last == "2026-W29"  # unchanged

    def test_noop_advances_and_no_rerun_on_restart(self, tmp_path: Path):
        calls: list[str] = []
        save_state(tmp_path, TidyState(weekly_last="2026-W29"))
        sched = self._sched(tmp_path, weekly_fn=lambda p: (calls.append(p), _ok(p))[1])
        now = datetime(2026, 7, 27, 8, 0)
        sched._catchup_scope_sync("weekly", now)
        assert calls == ["2026-W30"]
        assert load_state(tmp_path).weekly_last == "2026-W30"
        # second launch same time → no pending, fn not called
        calls.clear()
        sched._catchup_scope_sync("weekly", now)
        assert calls == []

    def test_monthly_catches_up_independently(self, tmp_path: Path):
        calls: list[str] = []
        save_state(tmp_path, TidyState(monthly_last="2026-05"))
        sched = self._sched(tmp_path, weekly_fn=_ok,
                            monthly_fn=lambda m: (calls.append(m), _ok(m))[1])
        sched._catchup_scope_sync("monthly", datetime(2026, 7, 27, 8, 0))
        # end = last_month(Jul 27) = 2026-06 → only 2026-06 pending
        assert calls == ["2026-06"]
        assert load_state(tmp_path).monthly_last == "2026-06"


class TestCatchupAllAndWeeklyFirst:
    """C-5: startup catchup runs weekly first; monthly only if weekly is clear,
    because the monthly report consumes weeklies."""

    def test_monthly_skipped_when_weekly_behind(self, tmp_path: Path):
        wcalls: list[str] = []
        mcalls: list[str] = []

        def wf(period: str) -> dict:
            wcalls.append(period)
            return _err(period) if period == "2026-W29" else _ok(period)

        def mf(period: str) -> dict:
            mcalls.append(period)
            return _ok(period)

        save_state(tmp_path, TidyState(weekly_last="2026-W27", monthly_last="2026-05"))
        sched = TidyScheduler(
            tmp_path, _noop_provider, now_fn=_NOW_W31, sleep_fn=_HangingSleep(),
            weekly_fn=wf, monthly_fn=mf,
        )
        sched._catchup_all_sync(datetime(2026, 7, 27, 8, 0))
        # weekly ran (and failed at W29 → still behind); monthly never ran
        assert "2026-W29" in wcalls
        assert mcalls == []
        assert load_state(tmp_path).monthly_last == "2026-05"  # untouched

    def test_monthly_runs_when_weekly_clear(self, tmp_path: Path):
        mcalls: list[str] = []
        save_state(tmp_path, TidyState(weekly_last="2026-W30", monthly_last="2026-05"))
        sched = TidyScheduler(
            tmp_path, _noop_provider, now_fn=_NOW_W31, sleep_fn=_HangingSleep(),
            weekly_fn=_ok, monthly_fn=lambda m: (mcalls.append(m), _ok(m))[1],
        )
        sched._catchup_all_sync(datetime(2026, 7, 27, 8, 0))
        # weekly already at W30 (no pending) → monthly runs: 2026-06
        assert mcalls == ["2026-06"]
        assert load_state(tmp_path).monthly_last == "2026-06"


class TestCrashRecovery:
    """C-6: state is written LAST. If we crash between the tidy succeeding and
    the watermark advancing, the watermark didn't move, so the next run safely
    re-runs the period (tidy itself is idempotent — stable plan id + apply
    snapshot replace)."""

    async def test_state_write_crash_then_safe_rerun(
        self, tmp_path: Path, monkeypatch
    ):
        import trowel_py.memory.tidy_scheduler as tsmod

        calls: list[str] = []
        save_state(tmp_path, TidyState(weekly_last="2026-W29"))
        sched = TidyScheduler(
            tmp_path, _noop_provider, now_fn=_NOW_W31, sleep_fn=_HangingSleep(),
            weekly_fn=lambda p: (calls.append(p), _ok(p))[1], monthly_fn=_ok,
        )
        now = datetime(2026, 7, 27, 8, 0)

        real_save = tsmod.save_state
        saves: list[int] = []

        def flaky(root, state):
            saves.append(1)
            if len(saves) == 1:
                raise RuntimeError("crash before state write")
            return real_save(root, state)

        monkeypatch.setattr(tsmod, "save_state", flaky)
        with pytest.raises(RuntimeError):
            sched._catchup_scope_sync("weekly", now)
        # watermark did NOT advance (state write crashed)
        assert load_state(tmp_path).weekly_last == "2026-W29"

        # rerun: second save succeeds — period retried once (safe rerun)
        sched._catchup_scope_sync("weekly", now)
        assert calls == ["2026-W30", "2026-W30"]
        assert load_state(tmp_path).weekly_last == "2026-W30"


# ---------- T4: loops fire catchup on wake ----------


class TestLoops:
    async def test_weekly_loop_runs_catchup_on_wake(self, tmp_path: Path):
        sleep = _BudgetSleep(budget=1)
        sched = TidyScheduler(
            tmp_path, _noop_provider, now_fn=lambda: datetime(2026, 7, 12, 1, 0),
            sleep_fn=sleep, weekly_fn=_ok, monthly_fn=_ok,
        )
        woke: list[str] = []

        async def fake_run(scope):
            woke.append(scope)

        sched._run_catchup_scope = fake_run  # type: ignore[method-assign]
        await sched._weekly_loop()
        assert woke == ["weekly"]

    async def test_monthly_loop_skips_when_weekly_behind(self, tmp_path: Path):
        save_state(tmp_path, TidyState(weekly_last="2026-W20"))  # weekly far behind
        sleep = _BudgetSleep(budget=1)
        sched = TidyScheduler(
            tmp_path, _noop_provider, now_fn=_NOW_W31, sleep_fn=sleep,
            weekly_fn=_ok, monthly_fn=_ok,
        )
        ran: list[str] = []

        async def fake_run(scope):
            ran.append(scope)

        sched._run_catchup_scope = fake_run  # type: ignore[method-assign]
        await sched._monthly_loop()
        assert ran == []  # C-5: monthly wake skipped, weekly still behind

    async def test_monthly_loop_runs_when_weekly_clear(self, tmp_path: Path):
        save_state(tmp_path, TidyState(weekly_last="2026-W30"))  # weekly current
        sleep = _BudgetSleep(budget=1)
        sched = TidyScheduler(
            tmp_path, _noop_provider, now_fn=_NOW_W31, sleep_fn=sleep,
            weekly_fn=_ok, monthly_fn=_ok,
        )
        ran: list[str] = []

        async def fake_run(scope):
            ran.append(scope)

        sched._run_catchup_scope = fake_run  # type: ignore[method-assign]
        await sched._monthly_loop()
        assert ran == ["monthly"]


# ---------- T5: start / stop ----------


class TestStartStop:
    async def test_start_runs_startup_catchup_in_thread(self, tmp_path: Path):
        calls: list[str] = []
        save_state(tmp_path, TidyState(weekly_last="2026-W29"))
        sched = TidyScheduler(
            tmp_path, _noop_provider, now_fn=_NOW_W31, sleep_fn=_HangingSleep(),
            weekly_fn=lambda p: (calls.append(p), _ok(p))[1], monthly_fn=_ok,
        )
        await sched.start()
        assert sched._started is True
        # startup catchup runs in a worker thread — poll for it (C-7: doesn't
        # block start(); doesn't block the event loop)
        for _ in range(200):
            if calls:
                break
            await asyncio.sleep(0.005)
        assert calls == ["2026-W30"]
        assert load_state(tmp_path).weekly_last == "2026-W30"
        await sched.stop()
        assert sched.tasks == ()

    async def test_start_is_idempotent(self, tmp_path: Path):
        sched = TidyScheduler(
            tmp_path, _noop_provider, now_fn=_NOW_W31, sleep_fn=_HangingSleep(),
            weekly_fn=_ok, monthly_fn=_ok,
        )
        await sched.start()
        n = len(sched.tasks)
        await sched.start()  # no-op
        assert len(sched.tasks) == n
        await sched.stop()

    async def test_stop_does_not_block_on_long_backlog(self, tmp_path: Path):
        # 3-week backlog + slow-ish fn; stop() should return promptly because
        # _stopping makes the catchup loop yield between periods.
        calls: list[str] = []

        def fn(period: str) -> dict:
            calls.append(period)
            return _ok(period)

        save_state(tmp_path, TidyState(weekly_last="2026-W27"))
        sched = TidyScheduler(
            tmp_path, _noop_provider, now_fn=_NOW_W31, sleep_fn=_HangingSleep(),
            weekly_fn=fn, monthly_fn=_ok,
        )
        await sched.start()
        # _stopping makes the catchup loop yield between periods; stop() must
        # return promptly even with a 3-week backlog still queued.
        await asyncio.wait_for(sched.stop(), timeout=5.0)
        assert sched.tasks == ()
        # catchup may have completed or been interrupted — either is fine; the
        # point is stop() returned and didn't deadlock.

    def test_default_times_off_phase(self):
        assert DEFAULT_WEEKLY_TIME == time(3, 30)
        assert DEFAULT_MONTHLY_TIME == time(4, 0)

    async def test_stop_keeps_stopping_true_for_lingering_thread(
        self, tmp_path: Path
    ):
        # a catchup worker thread can outlive stop() (asyncio cancel can't halt
        # to_thread's sync body); _stopping must stay True so the lingering
        # thread exits at the next between-period check instead of mutating
        # memory after teardown. start() resets it on the next launch.
        sched = TidyScheduler(
            tmp_path, _noop_provider, now_fn=_NOW_W31, sleep_fn=_HangingSleep(),
            weekly_fn=_ok, monthly_fn=_ok,
        )
        await sched.start()
        await sched.stop()
        assert sched._stopping is True
        await sched.start()
        assert sched._stopping is False  # start() resets
        await sched.stop()


# ---------- T6: lifespan integration ----------


class TestLifespanIntegration:
    """verify app.py lifespan wires the tidy scheduler in (startup) and out
    (shutdown). TestClient runs the real lifespan; resolve_memory_root + the
    sibling schedulers' dispatches + the tidy fns are monkeypatched so no real
    ~/.trowel write, cc spawn, or provider call happens (#46416). The startup
    catchup now fires, so run_weekly/monthly_tidy are stubbed to a success."""

    def test_startup_starts_tidy_scheduler(self, tmp_path: Path, monkeypatch):
        from fastapi.testclient import TestClient

        from trowel_py.app import create_app
        from trowel_py.memory import paths as mem_paths
        from trowel_py.memory.profile_distill import scheduler as ds_mod
        from trowel_py.memory.daily_review import scheduler as rs_mod
        from trowel_py.memory import tidy as tidy_mod

        monkeypatch.setattr(mem_paths, "resolve_memory_root", lambda: tmp_path)
        monkeypatch.setattr(rs_mod, "_default_dispatch", lambda _e: None)
        monkeypatch.setattr(ds_mod, "_default_dispatch", lambda _e: None)
        # startup catchup calls the real tidy fns — stub them to a success so
        # no provider is constructed and no real tidy runs.
        monkeypatch.setattr(tidy_mod, "run_weekly_tidy", lambda r, w, p: _ok(w))
        monkeypatch.setattr(tidy_mod, "run_monthly_tidy", lambda r, m, p, **k: _ok(m))
        app = create_app()
        with TestClient(app):
            sched = app.state.tidy_scheduler
            assert sched is not None
            assert sched._started is True
            # catchup + weekly loop + monthly loop
            assert len(sched.tasks) == 3
        # shutdown stopped everything
        assert app.state.tidy_scheduler.tasks == ()


# ---------- T7: explicit catchup (CLI --from) ----------


class TestRunExplicitCatchup:
    """CLI ``--from`` entry: explicit range, idempotent, never raises — a bad
    period or a tidy exception becomes a structured report (C-3)."""

    def test_bad_from_period_returns_error_report(self, tmp_path: Path):
        from trowel_py.memory.tidy_scheduler import run_explicit_catchup

        save_state(tmp_path, TidyState(weekly_last="2026-W29"))
        result = run_explicit_catchup(
            tmp_path, "weekly", "garbage", _noop_provider,
            now=datetime(2026, 7, 27, 8, 0),
        )
        assert "error" in result
        assert result["ran"] == []
        assert result["watermark"] == "2026-W29"  # unchanged

    def test_tidy_exception_is_caught_not_raised(
        self, tmp_path: Path, monkeypatch
    ):
        import trowel_py.memory.tidy as tidy_mod
        from trowel_py.memory.tidy_scheduler import run_explicit_catchup

        def boom(root, week, provider):  # noqa: ARG001
            raise RuntimeError("provider 5xx")

        monkeypatch.setattr(tidy_mod, "run_weekly_tidy", boom)
        save_state(tmp_path, TidyState(weekly_last="2026-W27"))
        result = run_explicit_catchup(
            tmp_path, "weekly", "2026-W28", _noop_provider,
            now=datetime(2026, 7, 27, 8, 0),
        )
        # W28 raised → failed_at=W28, nothing ran, watermark stays at W27
        assert result["failed_at"] == "2026-W28"
        assert result["ran"] == []
        assert result["watermark"] == "2026-W27"
        assert load_state(tmp_path).weekly_last == "2026-W27"

    def test_advances_watermark_through_range(
        self, tmp_path: Path, monkeypatch
    ):
        import trowel_py.memory.tidy as tidy_mod
        from trowel_py.memory.tidy_scheduler import run_explicit_catchup

        monkeypatch.setattr(tidy_mod, "run_weekly_tidy", lambda r, w, p: _ok(w))
        save_state(tmp_path, TidyState(weekly_last="2026-W27"))
        result = run_explicit_catchup(
            tmp_path, "weekly", "2026-W28", _noop_provider,
            now=datetime(2026, 7, 27, 8, 0),  # end = W30
        )
        assert result["ran"] == ["2026-W28", "2026-W29", "2026-W30"]
        assert result["watermark"] == "2026-W30"
        assert load_state(tmp_path).weekly_last == "2026-W30"
