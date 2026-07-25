"""派生日记的只读范围判定、stale 比较与依赖级联。"""

from __future__ import annotations

import re
import uuid
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import cast

from trowel_py.memory.compress.daily_generation import _DAILY_GENERATION_VERSION
from trowel_py.memory.compress.rollup import (
    _MONTHLY_GENERATION_VERSION,
    monthly_sources,
    source_hash,
    weekly_sources,
)
from trowel_py.memory.compress.weekly_generation import WEEKLY_GENERATION_VERSION
from trowel_py.memory.store import MemoryStore, _split_frontmatter

from .models import (
    RegenerationLayer,
    RegenerationMode,
    RegenerationPlan,
    RegenerationTarget,
)
from .storage import save_plan

_VERSIONS = {
    "daily": _DAILY_GENERATION_VERSION,
    "weekly": WEEKLY_GENERATION_VERSION,
    "monthly": _MONTHLY_GENERATION_VERSION,
}
_LAYER_DIR = {"daily": "daily", "weekly": "weekly", "monthly": "monthly"}
_LAYER_ORDER = {"daily": 0, "weekly": 1, "monthly": 2}


def _daily_periods(start: str, end: str) -> list[str]:
    first = date.fromisoformat(start)
    last = date.fromisoformat(end)
    if first > last:
        raise ValueError("from_period must not be after to_period")
    periods: list[str] = []
    current = first
    while current <= last:
        periods.append(current.isoformat())
        current += timedelta(days=1)
    return periods


def _parse_week(period: str) -> date:
    match = re.fullmatch(r"(\d{4})-W(\d{2})", period)
    if not match:
        raise ValueError(f"invalid ISO week: {period!r}")
    return date.fromisocalendar(int(match.group(1)), int(match.group(2)), 1)


def _week_periods(start: str, end: str) -> list[str]:
    first = _parse_week(start)
    last = _parse_week(end)
    if first > last:
        raise ValueError("from_period must not be after to_period")
    periods: list[str] = []
    current = first
    while current <= last:
        year, week, _weekday = current.isocalendar()
        periods.append(f"{year}-W{week:02d}")
        current += timedelta(days=7)
    return periods


def _parse_month(period: str) -> tuple[int, int]:
    match = re.fullmatch(r"(\d{4})-(\d{2})", period)
    if not match:
        raise ValueError(f"invalid month: {period!r}")
    year, month = int(match.group(1)), int(match.group(2))
    date(year, month, 1)
    return year, month


def _month_periods(start: str, end: str) -> list[str]:
    first = _parse_month(start)
    last = _parse_month(end)
    if first > last:
        raise ValueError("from_period must not be after to_period")
    periods: list[str] = []
    year, month = first
    while (year, month) <= last:
        periods.append(f"{year:04d}-{month:02d}")
        month += 1
        if month == 13:
            year += 1
            month = 1
    return periods


def _periods(layer: RegenerationLayer, start: str, end: str) -> list[str]:
    if layer == "daily":
        return _daily_periods(start, end)
    if layer == "weekly":
        return _week_periods(start, end)
    return _month_periods(start, end)


def _path(root: Path, layer: RegenerationLayer, period: str) -> Path:
    return root / "diary" / _LAYER_DIR[layer] / f"{period}.md"


def _expected_source_hash(
    root: Path,
    layer: RegenerationLayer,
    period: str,
) -> str | None:
    if layer == "daily":
        from trowel_py.memory.compress.daily_generation import _source_hash

        daily_sources = MemoryStore(root).project_daily_sources(period)
        return _source_hash(daily_sources) if daily_sources else None
    rollup_sources = (
        weekly_sources(root, period)
        if layer == "weekly"
        else monthly_sources(root, period)
    )
    return source_hash(rollup_sources) if rollup_sources else None


def _state_target(
    root: Path,
    layer: RegenerationLayer,
    period: str,
    mode: RegenerationMode,
) -> RegenerationTarget | None:
    expected_hash = _expected_source_hash(root, layer, period)
    if expected_hash is None:
        return None
    path = _path(root, layer, period)
    expected_version = _VERSIONS[layer]
    if not path.is_file():
        reason = "missing"
        current_hash = ""
        current_version = None
        differences: tuple[str, ...] = ("missing",)
    else:
        frontmatter, _body = _split_frontmatter(path.read_text(encoding="utf-8"))
        frontmatter = frontmatter or {}
        current_hash = str(frontmatter.get("source_hash") or "")
        raw_version = frontmatter.get("generation_version")
        current_version = int(raw_version) if isinstance(raw_version, int) else None
        status = str(frontmatter.get("generation_status") or "")
        differences_list: list[str] = []
        if current_hash != expected_hash:
            differences_list.append("source_hash")
        if current_version != expected_version:
            differences_list.append("generation_version")
        if status != "ok":
            differences_list.append("generation_status")
        differences = tuple(differences_list)
        if status in {"failed", "fallback"}:
            reason = "failed"
        elif differences:
            reason = "stale"
        else:
            reason = "current"
    if mode != "all" and reason != mode:
        return None
    selected_reason = "all" if mode == "all" else reason
    return RegenerationTarget(
        layer=layer,
        period=period,
        reason=selected_reason,
        live_exists=path.is_file(),
        current_source_hash=current_hash,
        expected_source_hash=expected_hash,
        current_generation_version=current_version,
        expected_generation_version=expected_version,
        differences=differences,
    )


def _week_for_day(day: str) -> str:
    year, week, _weekday = date.fromisoformat(day).isocalendar()
    return f"{year}-W{week:02d}"


def _month_for_week(week: str) -> str:
    return _parse_week(week).strftime("%Y-%m")


def _cascade_target(
    root: Path,
    layer: RegenerationLayer,
    period: str,
    dependencies: tuple[str, ...],
) -> RegenerationTarget:
    path = _path(root, layer, period)
    current_hash = ""
    current_version: int | None = None
    if path.is_file():
        frontmatter, _body = _split_frontmatter(path.read_text(encoding="utf-8"))
        if frontmatter:
            current_hash = str(frontmatter.get("source_hash") or "")
            raw_version = frontmatter.get("generation_version")
            if isinstance(raw_version, int):
                current_version = raw_version
    return RegenerationTarget(
        layer=layer,
        period=period,
        reason="upstream",
        dependencies=dependencies,
        live_exists=path.is_file(),
        current_source_hash=current_hash,
        expected_generation_version=_VERSIONS[layer],
        current_generation_version=current_version,
        differences=("upstream",),
    )


def plan_regeneration(
    root: Path | str,
    *,
    layer: str,
    from_period: str,
    to_period: str,
    mode: str,
) -> RegenerationPlan:
    """只读取 live 派生物并持久化一份不可变重生成计划。"""
    if layer not in _LAYER_ORDER:
        raise ValueError(f"invalid regeneration layer: {layer!r}")
    if mode not in {"missing", "failed", "stale", "all"}:
        raise ValueError(f"invalid regeneration mode: {mode!r}")
    typed_layer = cast("RegenerationLayer", layer)
    typed_mode = cast("RegenerationMode", mode)
    root_path = Path(root)
    targets: dict[str, RegenerationTarget] = {}
    for period in _periods(typed_layer, from_period, to_period):
        target = _state_target(root_path, typed_layer, period, typed_mode)
        if target is not None:
            targets[target.key] = target

    if typed_layer == "daily":
        weeks: dict[str, list[str]] = {}
        for target in tuple(targets.values()):
            week = _week_for_day(target.period)
            weeks.setdefault(week, []).append(target.key)
        for week, dependencies in weeks.items():
            target = _cascade_target(
                root_path, "weekly", week, tuple(sorted(dependencies))
            )
            targets[target.key] = target

    if typed_layer in {"daily", "weekly"}:
        weeks_for_month: dict[str, list[str]] = {}
        for target in tuple(targets.values()):
            if target.layer != "weekly":
                continue
            month = _month_for_week(target.period)
            weeks_for_month.setdefault(month, []).append(target.key)
        for month, dependencies in weeks_for_month.items():
            target = _cascade_target(
                root_path, "monthly", month, tuple(sorted(dependencies))
            )
            targets[target.key] = target

    ordered = tuple(
        sorted(targets.values(), key=lambda item: (_LAYER_ORDER[item.layer], item.period))
    )
    created_at = datetime.now().astimezone().isoformat()
    plan = RegenerationPlan(
        plan_id=f"regen-{uuid.uuid4().hex}",
        created_at=created_at,
        requested_layer=typed_layer,
        from_period=from_period,
        to_period=to_period,
        mode=typed_mode,
        targets=ordered,
    )
    save_plan(root_path, plan)
    return plan
