"""Tidy 水位的周期推进与待补范围。"""

from datetime import datetime

import pytest

from trowel_py.memory.tidy_state import (
    MAX_PENDING_MONTHS,
    MAX_PENDING_WEEKS,
    enumerate_pending_months,
    enumerate_pending_weeks,
    last_iso_week,
    last_month,
    next_iso_week,
    next_month,
)


class TestNextPeriod:
    def test_next_iso_week_simple(self):
        assert next_iso_week("2026-W28") == "2026-W29"

    def test_next_iso_week_cross_year(self):
        assert next_iso_week("2026-W53") == "2027-W01"

    def test_next_iso_week_into_w53_year(self):
        assert next_iso_week("2020-W52") == "2020-W53"

    def test_next_month_simple(self):
        assert next_month("2026-06") == "2026-07"

    def test_next_month_jan_to_feb(self):
        assert next_month("2026-01") == "2026-02"

    def test_next_month_cross_year(self):
        assert next_month("2026-12") == "2027-01"

    def test_parse_rejects_garbage(self):
        with pytest.raises(ValueError):
            next_iso_week("nope")
        with pytest.raises(ValueError):
            next_month("2026-13")
        with pytest.raises(ValueError):
            next_month("2026/06")


class TestEnumeratePendingWeeks:
    def test_bootstrap_only_most_recent(self):
        now = datetime(2026, 7, 27, 8, 0)
        assert last_iso_week(now) == "2026-W30"
        assert enumerate_pending_weeks(None, now) == ["2026-W30"]

    def test_three_week_gap_spec_scenario(self):
        now = datetime(2026, 7, 27, 8, 0)
        assert enumerate_pending_weeks("2026-W27", now) == [
            "2026-W28",
            "2026-W29",
            "2026-W30",
        ]

    def test_last_equals_end_no_rerun(self):
        now = datetime(2026, 7, 27, 8, 0)
        assert enumerate_pending_weeks("2026-W30", now) == []

    def test_cross_year(self):
        now = datetime(2027, 1, 20, 3, 30)
        assert last_iso_week(now) == "2027-W02"
        assert enumerate_pending_weeks("2026-W53", now) == [
            "2027-W01",
            "2027-W02",
        ]

    def test_never_includes_in_progress_week(self):
        now = datetime(2026, 7, 27, 8, 0)
        pending = enumerate_pending_weeks("2026-W29", now)
        assert pending == ["2026-W30"]
        assert "2026-W31" not in pending

    def test_watermark_ahead_of_now_is_empty(self):
        now = datetime(2026, 7, 27, 8, 0)
        assert enumerate_pending_weeks("2026-W40", now) == []

    def test_capped_so_corrupt_watermark_cannot_loop_forever(self):
        now = datetime(2026, 7, 27, 8, 0)
        pending = enumerate_pending_weeks("2000-W01", now)
        assert len(pending) == MAX_PENDING_WEEKS

    def test_bad_watermark_format_raises(self):
        now = datetime(2026, 7, 27, 8, 0)
        with pytest.raises(ValueError):
            enumerate_pending_weeks("garbage", now)


class TestEnumeratePendingMonths:
    def test_bootstrap_only_most_recent(self):
        now = datetime(2026, 8, 15, 4, 0)
        assert last_month(now) == "2026-07"
        assert enumerate_pending_months(None, now) == ["2026-07"]

    def test_one_month_gap(self):
        now = datetime(2026, 8, 15, 4, 0)
        assert enumerate_pending_months("2026-06", now) == ["2026-07"]

    def test_cross_year(self):
        now = datetime(2027, 2, 15, 4, 0)
        assert enumerate_pending_months("2026-11", now) == [
            "2026-12",
            "2027-01",
        ]

    def test_last_equals_end_no_rerun(self):
        now = datetime(2026, 8, 15, 4, 0)
        assert enumerate_pending_months("2026-07", now) == []

    def test_never_includes_in_progress_month(self):
        now = datetime(2026, 8, 15, 4, 0)
        assert "2026-08" not in enumerate_pending_months("2026-05", now)

    def test_capped(self):
        now = datetime(2026, 8, 15, 4, 0)
        pending = enumerate_pending_months("1800-01", now)
        assert len(pending) == MAX_PENDING_MONTHS
