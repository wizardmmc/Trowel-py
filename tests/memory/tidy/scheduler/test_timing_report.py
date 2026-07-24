"""Scheduler 的纯时间计算与成功门禁。"""

from datetime import datetime, time

import pytest

from trowel_py.memory.tidy_scheduler import (
    last_iso_week,
    last_month,
    seconds_until_next_monthday,
    seconds_until_next_weekday,
    tidy_succeeded,
)

from .support import error_report, ok_report, skipped_report


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
        assert seconds_until_next_weekday(
            now,
            0,
            time(3, 30),
        ) == pytest.approx(95400)

    def test_same_day_when_target_ahead(self):
        assert seconds_until_next_weekday(
            datetime(2026, 7, 13, 3, 0),
            0,
            time(3, 30),
        ) == pytest.approx(1800)

    def test_rolls_to_next_week_when_passed(self):
        assert seconds_until_next_weekday(
            datetime(2026, 7, 13, 4, 0),
            0,
            time(3, 30),
        ) == pytest.approx(603000)


class TestSecondsUntilNextMonthday:
    def test_mid_month_to_next_first(self):
        assert seconds_until_next_monthday(
            datetime(2026, 7, 15, 4, 0),
            1,
            time(4, 0),
        ) == pytest.approx(1468800)

    def test_same_day_when_target_ahead(self):
        assert seconds_until_next_monthday(
            datetime(2026, 7, 1, 3, 0),
            1,
            time(4, 0),
        ) == pytest.approx(3600)

    def test_rolls_to_next_month_when_passed(self):
        assert seconds_until_next_monthday(
            datetime(2026, 7, 1, 5, 0),
            1,
            time(4, 0),
        ) == pytest.approx(2674800)


class TestTidySucceeded:
    def test_clean_noop_is_success(self):
        assert tidy_succeeded(ok_report("2026-W28")) is True

    def test_success_with_operations(self):
        assert tidy_succeeded({"tidy": {"applied": ["a"], "operations": 1}}) is True

    def test_skipped_is_not_success(self):
        assert tidy_succeeded(skipped_report("2026-W28")) is False

    def test_apply_error_is_not_success(self):
        assert tidy_succeeded(error_report("2026-W28")) is False

    def test_non_dict_is_not_success(self):
        assert tidy_succeeded(None) is False
        assert tidy_succeeded("ok") is False

    def test_no_failure_signal_is_success(self):
        assert tidy_succeeded({"plan_id": "p"}) is True
