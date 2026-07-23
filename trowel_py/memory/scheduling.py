from __future__ import annotations

from datetime import datetime, time

_SECONDS_PER_DAY = 24 * 3600


def seconds_until(target: time, now: datetime) -> float:
    """计算从 now 到下一次目标时刻的秒数，恰好命中时顺延到次日。"""
    today_target = now.replace(
        hour=target.hour,
        minute=target.minute,
        second=0,
        microsecond=0,
    )
    delta = (today_target - now).total_seconds()
    if delta <= 0:
        delta += _SECONDS_PER_DAY
    return delta
