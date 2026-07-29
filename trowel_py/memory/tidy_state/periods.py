"""Tidy 水位使用的纯周期计算。"""

from __future__ import annotations

import re
from datetime import datetime, timedelta

MAX_PENDING_WEEKS = 520
MAX_PENDING_MONTHS = 1200

_ISO_WEEK_RE = re.compile(r"^(\d{4})-W(\d{2})$")
_MONTH_RE = re.compile(r"^(\d{4})-(\d{2})$")


def last_iso_week(now: datetime) -> str:
    """返回 ``now`` 所在周之前的 ISO 周。

    Args:
        now: 计算基准时间；只使用日期。

    Returns:
        ``YYYY-Www`` 格式的上一 ISO 周。

    Raises:
        OverflowError: 上一周超出 ``datetime`` 支持范围。
    """
    previous = (now - timedelta(days=7)).date()
    year, week, _ = previous.isocalendar()
    return f"{year:04d}-W{week:02d}"


def last_month(now: datetime) -> str:
    """返回 ``now`` 所在月份之前的月份。

    Args:
        now: 计算基准时间；只使用日期。

    Returns:
        ``YYYY-MM`` 格式的上一个月。

    Raises:
        OverflowError: 上一个月超出 ``datetime`` 支持范围。
    """
    first = now.date().replace(day=1)
    return (first - timedelta(days=1)).strftime("%Y-%m")


def _parse_iso_week(s: str) -> tuple[int, int]:
    """解析 ``YYYY-Www`` 文本并校验真实 ISO 周。

    Args:
        s: 待解析文本。

    Returns:
        ISO 年和周序号。

    Raises:
        ValueError: 格式不符或该年没有指定周。
    """
    match = _ISO_WEEK_RE.match(s)
    if not match:
        raise ValueError(f"bad ISO week string: {s!r}")
    year, week = int(match.group(1)), int(match.group(2))
    datetime.fromisocalendar(year, week, 1)
    return year, week


def _parse_month(s: str) -> tuple[int, int]:
    """解析 ``YYYY-MM`` 文本并校验月份范围。

    年份只要求四位数字，不额外验证 ``datetime`` 是否支持。

    Args:
        s: 待解析文本。

    Returns:
        年和月。

    Raises:
        ValueError: 格式不符或月份不在 1 至 12。
    """
    match = _MONTH_RE.match(s)
    if not match:
        raise ValueError(f"bad month string: {s!r}")
    year, month = int(match.group(1)), int(match.group(2))
    if not 1 <= month <= 12:
        raise ValueError(f"bad month string: {s!r}")
    return year, month


def _valid_period(value: object, scope: str) -> str | None:
    """保留合法周期文本，把其他值降级为空水位。

    ``scope == "weekly"`` 时按 ISO 周校验，其他 scope 一律按月份校验。

    Args:
        value: 待校验的水位值。
        scope: 周级或其他范围标识。

    Returns:
        原周期文本；类型或格式无效时为 ``None``。
    """
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
    """返回合法 ISO 周的下一个周期。

    Args:
        s: ``YYYY-Www`` 格式的 ISO 周。

    Returns:
        跨年及第 53 周规则正确的下一 ISO 周。

    Raises:
        ValueError: 输入不是合法 ISO 周。
        OverflowError: 下一周超出 ``datetime`` 支持范围。
    """
    year, week = _parse_iso_week(s)
    monday = datetime.fromisocalendar(year, week, 1) + timedelta(days=7)
    next_year, next_week, _ = monday.isocalendar()
    return f"{next_year:04d}-W{next_week:02d}"


def next_month(s: str) -> str:
    """返回月份文本的下一个周期。

    输入年份只受四位格式约束；``9999-12`` 会生成无法再由本模块解析的
    ``10000-01``。

    Args:
        s: ``YYYY-MM`` 格式的月份。

    Returns:
        下一个月份，必要时跨年。

    Raises:
        ValueError: 输入格式或月份无效。
    """
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
    """按旧到新枚举周水位之后的已完成周。

    无水位时只返回最近一个已完成周，并忽略 ``cap``。有水位时从其下一周开始，
    最多返回 ``cap`` 个最早欠账；若水位的下一周晚于最近已完成周，或 ``cap``
    非正，则返回空列表。

    Args:
        last_successful: 最近成功的 ISO 周；``None`` 表示尚无水位。
        now: 确定最近已完成周的基准时间。
        cap: 有水位时最多返回的周期数。

    Returns:
        不包含当前进行中周的周期列表。

    Raises:
        ValueError: 非空水位不是合法 ISO 周。
        OverflowError: 基准时间的上一周或水位的下一周超出 ``datetime`` 支持范围。
    """
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
    """按旧到新枚举月水位之后的已完成月份。

    无水位时只返回最近一个已完成月，并忽略 ``cap``。有水位时从其下一月开始，
    最多返回 ``cap`` 个最早欠账；若 ``cap`` 非正，或四位年份的下一月晚于最近
    已完成月，则返回空列表。极端水位 ``9999-12`` 在 ``cap`` 为一时返回
    ``10000-01``，在更大的 ``cap`` 下会于下一轮解析时抛出 ``ValueError``。

    Args:
        last_successful: 最近成功月份；``None`` 表示尚无水位。
        now: 确定最近已完成月的基准时间。
        cap: 有水位时最多返回的周期数。

    Returns:
        不包含当前进行中月份的周期列表。

    Raises:
        ValueError: 非空水位的格式或月份无效，或推进后得到无法解析的五位年份。
        OverflowError: 基准时间的上一个月超出 ``datetime`` 支持范围。
    """
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
