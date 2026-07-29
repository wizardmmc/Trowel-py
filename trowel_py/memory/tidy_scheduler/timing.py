"""Scheduler 的纯时间计算。"""

from __future__ import annotations

from datetime import datetime, time, timedelta


def seconds_until_next_weekday(now: datetime, weekday: int, target: time) -> float:
    """计算到下一个目标星期和时分的秒数。

    候选时间必须严格晚于 ``now``，相等时顺延七天。``weekday`` 通过模 7
    参与计算，函数不校验其范围；``target`` 的秒、微秒和时区信息会被忽略，
    候选沿用 ``now`` 的时区信息。

    Args:
        now: 计算起点。
        weekday: 目标星期序号，星期一为 0。
        target: 目标时刻，仅使用小时和分钟。

    Returns:
        到候选时间的正秒数。
    """
    days_ahead = (weekday - now.weekday()) % 7
    candidate = (now + timedelta(days=days_ahead)).replace(
        hour=target.hour, minute=target.minute, second=0, microsecond=0
    )
    if candidate <= now:
        candidate += timedelta(days=7)
    return (candidate - now).total_seconds()


def seconds_until_next_monthday(now: datetime, day: int, target: time) -> float:
    """逐月寻找严格晚于起点的目标日和时分。

    不存在该日期的月份会被跳过，最多检查 24 个月；仍未找到时固定返回一天。
    ``target`` 只使用小时和分钟。候选由不带时区的 ``datetime`` 构造，因此
    ``now`` 带时区且遇到合法候选时，比较候选与 ``now`` 会抛出 ``TypeError``。

    Args:
        now: 计算起点。
        day: 目标月内日期；函数不预先校验范围。
        target: 目标时刻，仅使用小时和分钟。

    Returns:
        到候选时间的秒数；24 个月内没有合法候选时为 86400。
    """
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
