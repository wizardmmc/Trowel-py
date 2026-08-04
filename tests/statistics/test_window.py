"""固定统计日期范围的时区、上限和夏令时语义。"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from trowel_py.statistics.window import parse_statistics_window


def test_date_window_is_inclusive_and_preserves_requested_timezone() -> None:
    window = parse_statistics_window(
        date(2026, 8, 1),
        date(2026, 8, 3),
        "Asia/Shanghai",
    )

    assert window.timezone == "Asia/Shanghai"
    assert window.start.isoformat() == "2026-08-01T00:00:00+08:00"
    assert window.end.isoformat() == "2026-08-04T00:00:00+08:00"


def test_date_window_uses_local_midnights_across_dst() -> None:
    window = parse_statistics_window(
        date(2026, 3, 8),
        date(2026, 3, 8),
        "America/New_York",
    )

    assert window.end - window.start == timedelta(hours=23)


@pytest.mark.parametrize(
    ("start", "end", "timezone_name"),
    [
        (date(2026, 8, 3), date(2026, 8, 2), "UTC"),
        (date(2025, 1, 1), date(2026, 8, 3), "UTC"),
        (date(2026, 8, 1), date(2026, 8, 3), "Mars/Olympus"),
    ],
)
def test_date_window_rejects_reversed_unbounded_and_unknown_ranges(
    start: date,
    end: date,
    timezone_name: str,
) -> None:
    with pytest.raises(ValueError):
        parse_statistics_window(start, end, timezone_name)
