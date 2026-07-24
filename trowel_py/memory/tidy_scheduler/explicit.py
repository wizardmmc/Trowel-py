"""CLI 显式补跑入口。"""

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
    """补跑显式范围，首个失败停止，并返回不抛异常的结构化结果。"""
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
            return run_weekly_tidy(root, period, provider_factory())

        step = next_iso_week
    else:
        end = last_month(now)
        cap = MAX_PENDING_MONTHS

        def fn(period: str) -> Any:
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
