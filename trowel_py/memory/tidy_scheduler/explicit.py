"""按 CLI 指定的起始周期顺序补跑周级或月级 Tidy。"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import Any

from trowel_py.memory.tidy_state import (
    MAX_PENDING_MONTHS,
    MAX_PENDING_WEEKS,
    advance_watermark,
    last_iso_week,
    last_month,
    load_state,
    next_iso_week,
    next_month,
)

from .report import tidy_succeeded
from .types import ProviderFactory, Scope

logger = logging.getLogger("trowel_py.memory.tidy_scheduler")


def run_explicit_catchup(
    root: Path,
    scope: Scope,
    from_period: str,
    provider_factory: ProviderFactory,
    *,
    now: datetime | None = None,
) -> dict[str, object]:
    """从指定周期补跑至最近一个已完成周期。

    起始周期独立于当前水位，可以早于或晚于它；计划数量受对应上限约束。每个
    成功周期立即覆盖水位；若起始周期早于当前水位，水位可能先回退，后续失败
    不会恢复调用前值。首个失败报告会停止后续周期；提供者工厂或 Tidy 抛出的
    异常会记入日志并将该周期标为失败。周期格式错误也会转成返回结果，状态读取、
    保存等其他异常仍会传播。

    Args:
        root: 记忆目录。
        scope: ``weekly`` 或 ``monthly``。
        from_period: 纳入补跑的第一个周期。
        provider_factory: 每个尝试运行的周期调用一次的模型提供者工厂。
        now: 用于确定最近已完成周期和记录水位时间；省略时取本机当前时间。

    Returns:
        包含 ``scope``、``from``、``planned``、``ran``、``failed_at`` 和
        ``watermark``；起始周期非法时另含 ``error``。提供者工厂或 Tidy
        抛出的异常只写日志，不放入返回值。
    """
    from trowel_py.memory.tidy import run_monthly_tidy, run_weekly_tidy

    now = now or datetime.now()
    state = load_state(root)
    watermark = state.weekly_last if scope == "weekly" else state.monthly_last
    try:
        if scope == "weekly":
            next_iso_week(from_period)
        else:
            next_month(from_period)
    except ValueError as exc:
        return {
            "scope": scope,
            "from": from_period,
            "planned": [],
            "ran": [],
            "failed_at": None,
            "watermark": watermark,
            "error": f"bad --from period: {exc}",
        }

    if scope == "weekly":
        end = last_iso_week(now)
        cap = MAX_PENDING_WEEKS

        def fn(period: str) -> Any:
            """调用提供者工厂并运行指定的周周期。"""
            return run_weekly_tidy(root, period, provider_factory())

        step = next_iso_week
    else:
        end = last_month(now)
        cap = MAX_PENDING_MONTHS

        def fn(period: str) -> Any:
            """调用提供者工厂并运行指定的月周期。"""
            return run_monthly_tidy(root, period, provider_factory())

        step = next_month

    periods: list[str] = []
    current = from_period
    while current <= end and len(periods) < cap:
        periods.append(current)
        current = step(current)

    ran: list[str] = []
    failed_at: str | None = None
    for period in periods:
        try:
            report = fn(period)
        except Exception:
            logger.exception(
                "[memory] %s catchup (%s) raised — watermark stays at %s",
                scope,
                period,
                watermark,
            )
            failed_at = period
            break
        if not tidy_succeeded(report):
            failed_at = period
            break
        advance_watermark(root, scope, period, now)
        ran.append(period)
        watermark = period

    return {
        "scope": scope,
        "from": from_period,
        "planned": periods,
        "ran": ran,
        "failed_at": failed_at,
        "watermark": watermark,
    }
