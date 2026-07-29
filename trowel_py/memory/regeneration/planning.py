"""检查 live 派生日记的状态，并生成带依赖的重生成计划。"""

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
    """把有效日期范围展开为包含首尾的 ISO 日期列表。

    Raises:
        ValueError: 日期格式无效，或开始日期晚于结束日期。
        OverflowError: 范围包含最大支持日期，循环递增时越界。
    """
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
    """把 ``YYYY-Www`` 周标识解析为该 ISO 周的周一。

    Raises:
        ValueError: 格式无效，或年份与周数组合不存在。
    """
    match = re.fullmatch(r"(\d{4})-W(\d{2})", period)
    if not match:
        raise ValueError(f"invalid ISO week: {period!r}")
    return date.fromisocalendar(int(match.group(1)), int(match.group(2)), 1)


def _week_periods(start: str, end: str) -> list[str]:
    """把有效 ISO 周范围展开为包含首尾的周标识列表。

    Raises:
        ValueError: 周标识无效，或开始周晚于结束周。
        OverflowError: 范围末周接近最大支持日期，循环递增时越界。
    """
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
    """把 ``YYYY-MM`` 标识解析为年份和月份。

    Raises:
        ValueError: 格式无效或月份不存在。
    """
    match = re.fullmatch(r"(\d{4})-(\d{2})", period)
    if not match:
        raise ValueError(f"invalid month: {period!r}")
    year, month = int(match.group(1)), int(match.group(2))
    date(year, month, 1)
    return year, month


def _month_periods(start: str, end: str) -> list[str]:
    """把有效月份范围展开为包含首尾的 ``YYYY-MM`` 列表。

    Raises:
        ValueError: 月份标识无效，或开始月份晚于结束月份。
    """
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
    """按已校验的派生层展开请求范围内的周期。"""
    if layer == "daily":
        return _daily_periods(start, end)
    if layer == "weekly":
        return _week_periods(start, end)
    return _month_periods(start, end)


def _path(root: Path, layer: RegenerationLayer, period: str) -> Path:
    """返回指定派生层和周期的 live 日记路径。"""
    return root / "diary" / _LAYER_DIR[layer] / f"{period}.md"


def _expected_source_hash(
    root: Path,
    layer: RegenerationLayer,
    period: str,
) -> str | None:
    """计算当前上游内容的来源哈希；没有上游内容时返回 ``None``。"""
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
    """比较 live 派生物与当前来源，并按模式决定是否生成目标。

    没有上游内容时直接跳过；有上游但没有 live 文件时归入 ``missing``。
    已有文件且状态为 ``failed`` 或 ``fallback`` 时优先归入 ``failed``，否则
    来源哈希、生成版本或状态任一不符便归入 ``stale``，全部相同才是
    ``current``。``all`` 会选中这些非跳过目标，并把目标 reason 写为
    ``all``。版本仅以 ``isinstance(value, int)`` 读取，因此 bool 也会作为
    整数参与比较，其他类型记为 ``None``。
    """
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
    """返回 ISO 日期所属的 ``YYYY-Www`` 周标识。"""
    year, week, _weekday = date.fromisoformat(day).isocalendar()
    return f"{year}-W{week:02d}"


def _month_for_week(week: str) -> str:
    """返回 ISO 周的周一所属的 ``YYYY-MM`` 月份。"""
    return _parse_week(week).strftime("%Y-%m")


def _cascade_target(
    root: Path,
    layer: RegenerationLayer,
    period: str,
    dependencies: tuple[str, ...],
) -> RegenerationTarget:
    """为受上游重生成影响的周或月创建级联目标。

    规划时尚无新的上游产物，因此不会计算预期来源哈希；live 文件存在时，
    读取其中的来源哈希和生成版本。执行器会等 ``dependencies`` 全部成功后
    再生成并校验产物。live 版本只接受 ``isinstance(value, int)`` 的值，
    包括 bool，其他类型记为 ``None``。
    """
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
    """检查请求范围并持久化一份不可变重生成计划。

    本函数不改写 live 派生物。所选日目标按 ISO 周合并为一个周目标，该周目标
    依赖同周全部所选日目标；月目标再依赖周一落在该月的全部周目标。周请求
    同样按周一所属月份生成月目标。每个目标的依赖 key 会排序，最终目标按日、
    周、月及各层周期排序。即使没有目标，也会保存并返回空计划。

    Args:
        root: memory 根目录。
        layer: 起始派生层，必须是 ``daily``、``weekly`` 或 ``monthly``。
        from_period: 起始层请求范围的首个周期，包含在范围内。
        to_period: 起始层请求范围的末个周期，包含在范围内。
        mode: ``missing``、``failed``、``stale`` 或 ``all``。

    Returns:
        已写入 ``meta/regeneration/plans`` 的新计划。

    Raises:
        ValueError: 层级、模式或周期范围无效。
        OverflowError: 日或周范围递增超出日期类型上限。
        UnicodeError: 上游来源文件或 live 派生文件不是有效 UTF-8。
        OSError: 读取上游来源或 live 派生文件，或保存计划失败。
    """
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
