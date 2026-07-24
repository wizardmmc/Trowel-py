"""Tidy 水位使用的纯周期计算。"""

from __future__ import annotations

import re
from datetime import datetime, timedelta

MAX_PENDING_WEEKS = 520
MAX_PENDING_MONTHS = 1200

_ISO_WEEK_RE = re.compile(r"^(\d{4})-W(\d{2})$")
_MONTH_RE = re.compile(r"^(\d{4})-(\d{2})$")


def last_iso_week(now: datetime) -> str:
    """返回 now 所在周之前最近一个已完成的 ISO week。"""
    previous = (now - timedelta(days=7)).date()
    year, week, _ = previous.isocalendar()
    return f"{year:04d}-W{week:02d}"


def last_month(now: datetime) -> str:
    """返回 now 所在月份之前最近一个已完成的月份。"""
    first = now.date().replace(day=1)
    return (first - timedelta(days=1)).strftime("%Y-%m")


def _parse_iso_week(s: str) -> tuple[int, int]:
    match = _ISO_WEEK_RE.match(s)
    if not match:
        raise ValueError(f"bad ISO week string: {s!r}")
    year, week = int(match.group(1)), int(match.group(2))
    datetime.fromisocalendar(year, week, 1)
    return year, week


def _parse_month(s: str) -> tuple[int, int]:
    match = _MONTH_RE.match(s)
    if not match:
        raise ValueError(f"bad month string: {s!r}")
    year, month = int(match.group(1)), int(match.group(2))
    if not 1 <= month <= 12:
        raise ValueError(f"bad month string: {s!r}")
    return year, month


def _valid_period(value: object, scope: str) -> str | None:
    """非法水位降级为 None，让读取方走保守 bootstrap。"""
    if not isinstance(value, str):
        return None
    try:
        if scope == "weekly":
            _parse_iso_week(value)
        else:
            _parse_month(value)
    except ValueError:
        return None
    return value


def next_iso_week(s: str) -> str:
    """返回下一个 ISO week，覆盖跨年和 W53。"""
    year, week = _parse_iso_week(s)
    monday = datetime.fromisocalendar(year, week, 1) + timedelta(days=7)
    next_year, next_week, _ = monday.isocalendar()
    return f"{next_year:04d}-W{next_week:02d}"


def next_month(s: str) -> str:
    """返回下一个月份。"""
    year, month = _parse_month(s)
    month += 1
    if month > 12:
        month, year = 1, year + 1
    return f"{year:04d}-{month:02d}"


def enumerate_pending_weeks(
    last_successful: str | None,
    now: datetime,
    *,
    cap: int = MAX_PENDING_WEEKS,
) -> list[str]:
    """按旧到新返回水位之后的已完成周；无水位时只返回最近一周。"""
    end = last_iso_week(now)
    if last_successful is None:
        return [end]
    pending: list[str] = []
    current = last_successful
    while len(pending) < cap:
        following = next_iso_week(current)
        if following > end:
            break
        pending.append(following)
        current = following
    return pending


def enumerate_pending_months(
    last_successful: str | None,
    now: datetime,
    *,
    cap: int = MAX_PENDING_MONTHS,
) -> list[str]:
    """按旧到新返回水位之后的已完成月；无水位时只返回最近一月。"""
    end = last_month(now)
    if last_successful is None:
        return [end]
    pending: list[str] = []
    current = last_successful
    while len(pending) < cap:
        following = next_month(current)
        if following > end:
            break
        pending.append(following)
        current = following
    return pending
