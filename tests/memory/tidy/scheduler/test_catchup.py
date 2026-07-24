"""Scheduler 的补跑顺序、失败停止与水位推进。"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from trowel_py.memory.tidy_scheduler import TidyScheduler
from trowel_py.memory.tidy_state import TidyState, load_state, save_state

from .support import (
    HangingSleep,
    error_report,
    noop_provider,
    now_w31,
    ok_report,
    recording_success,
    skipped_report,
)


def make_scheduler(
    root: Path,
    *,
    weekly_fn,
    monthly_fn=ok_report,
) -> TidyScheduler:
    return TidyScheduler(
        root,
        noop_provider,
        now_fn=now_w31,
        sleep_fn=HangingSleep(),
        weekly_fn=weekly_fn,
        monthly_fn=monthly_fn,
    )


class TestCatchupScope:
    def test_three_week_gap_advances_one_at_a_time(self, tmp_path: Path):
        calls: list[str] = []
        save_state(tmp_path, TidyState(weekly_last="2026-W27"))
        scheduler = make_scheduler(
            tmp_path,
            weekly_fn=recording_success(calls),
        )
        scheduler._catchup_scope_sync(
            "weekly",
            datetime(2026, 7, 27, 8, 0),
        )
        assert calls == ["2026-W28", "2026-W29", "2026-W30"]
        assert load_state(tmp_path).weekly_last == "2026-W30"

    def test_failure_stops_scope_and_keeps_watermark(self, tmp_path: Path):
        calls: list[str] = []

        def run(period: str) -> dict:
            calls.append(period)
            return error_report(period) if period == "2026-W29" else ok_report(period)

        save_state(tmp_path, TidyState(weekly_last="2026-W27"))
        scheduler = make_scheduler(tmp_path, weekly_fn=run)
        scheduler._catchup_scope_sync(
            "weekly",
            datetime(2026, 7, 27, 8, 0),
        )
        assert calls == ["2026-W28", "2026-W29"]
        assert load_state(tmp_path).weekly_last == "2026-W28"

    def test_exception_stops_scope_and_keeps_watermark(self, tmp_path: Path):
        calls: list[str] = []

        def run(period: str) -> dict:
            calls.append(period)
            if period == "2026-W29":
                raise RuntimeError("provider exploded")
            return ok_report(period)

        save_state(tmp_path, TidyState(weekly_last="2026-W27"))
        scheduler = make_scheduler(tmp_path, weekly_fn=run)
        scheduler._catchup_scope_sync(
            "weekly",
            datetime(2026, 7, 27, 8, 0),
        )
        assert calls == ["2026-W28", "2026-W29"]
        assert load_state(tmp_path).weekly_last == "2026-W28"

    def test_skipped_lock_does_not_advance(self, tmp_path: Path):
        save_state(tmp_path, TidyState(weekly_last="2026-W29"))
        scheduler = make_scheduler(tmp_path, weekly_fn=skipped_report)
        scheduler._catchup_scope_sync(
            "weekly",
            datetime(2026, 7, 27, 8, 0),
        )
        assert load_state(tmp_path).weekly_last == "2026-W29"

    def test_noop_advances_and_no_rerun_on_restart(self, tmp_path: Path):
        calls: list[str] = []
        save_state(tmp_path, TidyState(weekly_last="2026-W29"))
        scheduler = make_scheduler(
            tmp_path,
            weekly_fn=recording_success(calls),
        )
        now = datetime(2026, 7, 27, 8, 0)
        scheduler._catchup_scope_sync("weekly", now)
        assert calls == ["2026-W30"]
        assert load_state(tmp_path).weekly_last == "2026-W30"
        calls.clear()
        scheduler._catchup_scope_sync("weekly", now)
        assert calls == []

    def test_monthly_catches_up_independently(self, tmp_path: Path):
        calls: list[str] = []
        save_state(tmp_path, TidyState(monthly_last="2026-05"))
        scheduler = make_scheduler(
            tmp_path,
            weekly_fn=ok_report,
            monthly_fn=recording_success(calls),
        )
        scheduler._catchup_scope_sync(
            "monthly",
            datetime(2026, 7, 27, 8, 0),
        )
        assert calls == ["2026-06"]
        assert load_state(tmp_path).monthly_last == "2026-06"


class TestCatchupAllAndWeeklyFirst:
    def test_monthly_skipped_when_weekly_behind(self, tmp_path: Path):
        weekly_calls: list[str] = []
        monthly_calls: list[str] = []

        def weekly(period: str) -> dict:
            weekly_calls.append(period)
            return error_report(period) if period == "2026-W29" else ok_report(period)

        def monthly(period: str) -> dict:
            monthly_calls.append(period)
            return ok_report(period)

        save_state(
            tmp_path,
            TidyState(
                weekly_last="2026-W27",
                monthly_last="2026-05",
            ),
        )
        scheduler = make_scheduler(
            tmp_path,
            weekly_fn=weekly,
            monthly_fn=monthly,
        )
        scheduler._catchup_all_sync(datetime(2026, 7, 27, 8, 0))
        assert "2026-W29" in weekly_calls
        assert monthly_calls == []
        assert load_state(tmp_path).monthly_last == "2026-05"

    def test_monthly_runs_when_weekly_clear(self, tmp_path: Path):
        monthly_calls: list[str] = []
        save_state(
            tmp_path,
            TidyState(
                weekly_last="2026-W30",
                monthly_last="2026-05",
            ),
        )
        scheduler = make_scheduler(
            tmp_path,
            weekly_fn=ok_report,
            monthly_fn=recording_success(monthly_calls),
        )
        scheduler._catchup_all_sync(datetime(2026, 7, 27, 8, 0))
        assert monthly_calls == ["2026-06"]
        assert load_state(tmp_path).monthly_last == "2026-06"


class TestCrashRecovery:
    async def test_state_write_crash_then_safe_rerun(
        self,
        tmp_path: Path,
        monkeypatch,
    ):
        import trowel_py.memory.tidy_scheduler as scheduler_module

        calls: list[str] = []
        save_state(tmp_path, TidyState(weekly_last="2026-W29"))
        scheduler = make_scheduler(
            tmp_path,
            weekly_fn=recording_success(calls),
        )
        now = datetime(2026, 7, 27, 8, 0)

        real_save = scheduler_module.save_state
        saves: list[int] = []

        def flaky(root, state):
            saves.append(1)
            if len(saves) == 1:
                raise RuntimeError("crash before state write")
            return real_save(root, state)

        monkeypatch.setattr(scheduler_module, "save_state", flaky)
        with pytest.raises(RuntimeError):
            scheduler._catchup_scope_sync("weekly", now)
        assert load_state(tmp_path).weekly_last == "2026-W29"

        scheduler._catchup_scope_sync("weekly", now)
        assert calls == ["2026-W30", "2026-W30"]
        assert load_state(tmp_path).weekly_last == "2026-W30"
