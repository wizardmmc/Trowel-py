"""计算 Memory 每日任务按当前时钟等待的秒数。"""

from __future__ import annotations

from datetime import datetime, time

_SECONDS_PER_DAY = 24 * 3600


def seconds_until(target: time, now: datetime) -> float:
    """计算到下一次目标小时和分钟的等待秒数。

    目标的秒、微秒、时区和 fold 会被忽略；候选时刻继承 ``now`` 的日期、
    时区和 fold，并固定为第 0 秒。候选不严格晚于 ``now`` 时增加固定
    86400 秒，因此这是墙上时钟式的日循环，不按 DST 日的实际长短修正。

    Args:
        target: 每日目标小时和分钟。
        now: 计算起点；可为 naive 或带时区 datetime。

    Returns:
        严格大于零的等待秒数。
    """
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
