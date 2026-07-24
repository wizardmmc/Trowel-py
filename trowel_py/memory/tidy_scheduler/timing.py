"""Scheduler 的纯时间计算。"""

from __future__ import annotations

from datetime import datetime, time, timedelta


def seconds_until_next_weekday(now: datetime, weekday: int, target: time) -> float:
    """计算严格晚于 now 的下一个目标星期与时刻。"""
    days_ahead = (weekday - now.weekday()) % 7
    candidate = (now + timedelta(days=days_ahead)).replace(
        hour=target.hour, minute=target.minute, second=0, microsecond=0
    )
    if candidate <= now:
        candidate += timedelta(days=7)
    return (candidate - now).total_seconds()


def seconds_until_next_monthday(now: datetime, day: int, target: time) -> float:
    """逐月寻找严格晚于 now 的目标日期与时刻。"""
    year, month = now.year, now.month
    for _ in range(24):
        try:
            candidate = datetime(
                year,
                month,
                day,
                target.hour,
                target.minute,
                0,
                0,
            )
        except ValueError:
            pass
        else:
            if candidate > now:
                return (candidate - now).total_seconds()
        month += 1
        if month > 12:
            month, year = 1, year + 1
    return float(24 * 3600)
