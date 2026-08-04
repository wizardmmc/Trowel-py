"""把用户日期和 IANA 时区转换成有界、明确的统计时间窗。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

MAX_WINDOW_DAYS = 366


@dataclass(frozen=True)
class StatisticsWindow:
    """表示 Statistics API 查询使用的本地日期范围。

    Attributes:
        start: 首日当地零点，包含在查询中。
        end: 末日次日当地零点，不包含在查询中。
        timezone: 调用方请求的 IANA 时区名称。
    """

    start: datetime
    end: datetime
    timezone: str


def parse_statistics_window(
    start_date: date,
    end_date: date,
    timezone_name: str,
) -> StatisticsWindow:
    """解析首尾均包含的日期范围，并限制查询最多 366 个自然日。

    Args:
        start_date: 查询包含的第一个当地日期。
        end_date: 查询包含的最后一个当地日期。
        timezone_name: 用于解释当地零点的 IANA 时区名称。

    Returns:
        带明确 UTC offset 的半开时间窗和原始时区名称。

    Raises:
        ValueError: 时区不存在、日期倒置或范围超过 366 天。
    """

    if end_date < start_date:
        raise ValueError("end_date must not be before start_date")
    if (end_date - start_date).days + 1 > MAX_WINDOW_DAYS:
        raise ValueError("statistics window exceeds 366 days")
    try:
        timezone = ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError as exc:
        raise ValueError(f"unknown timezone: {timezone_name}") from exc
    local_start = datetime.combine(start_date, time.min, timezone)
    local_end = datetime.combine(end_date + timedelta(days=1), time.min, timezone)
    # 固定各边界当时的 UTC offset，使直接相减也反映夏令时跨越的真实小时数。
    start = datetime.fromisoformat(local_start.isoformat())
    end = datetime.fromisoformat(local_end.isoformat())
    return StatisticsWindow(start=start, end=end, timezone=timezone_name)
