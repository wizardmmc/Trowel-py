"""执行各 runtime 共用的 refine、持久化、水位推进和 judge 流程。"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from pathlib import Path

from trowel_py.memory.daily_review.agent import (
    DistillError,
    HostFactory,
    run_one_session,
)
from trowel_py.memory.daily_review.models import ReviewUnit
from trowel_py.memory.draft import procedure_warnings
from trowel_py.memory.dualtrack import audit_draft
from trowel_py.memory.judge import judge_session
from trowel_py.memory.persist import persist_draft
from trowel_py.memory.provenance import DerivationProvenance
from trowel_py.memory.store import MemoryStore

logger = logging.getLogger("trowel_py.memory.review_job")


async def process_review_units(
    root: Path,
    date_str: str,
    units: Iterable[ReviewUnit],
    store: MemoryStore,
    *,
    host_factory: HostFactory | None,
) -> set[str]:
    """用同一条流程处理 runtime 已准备好的待提炼单元。

    来源不可用、提炼失败、日期越界、持久化不完整或水位推进失败时保留原
    水位。只有持久化和水位推进都成功的 Diary 日期才返回给调用方重建 Daily；
    judge 是旁路步骤，失败不撤销已经落盘的事实。

    Args:
        root: Memory 根目录。
        date_str: 本次 review 工作目录和持久化记录使用的日期。
        units: runtime 提供的待提炼单元。
        store: 接收 Note、Episode、meta 和 completion manifest 的 MemoryStore。
        host_factory: 创建提炼 host 的可选工厂。

    Returns:
        已成功落盘并推进水位的 Diary 日期集合。
    """
    touched_dates: set[str] = set()
    for unit in units:
        try:
            review_source = unit.build_source()
        except ValueError as exc:
            logger.warning(
                "review source invalid for %s (not advanced): %s",
                unit.label,
                exc,
            )
            continue

        derivation: DerivationProvenance | None = None

        def capture_derivation(value: DerivationProvenance) -> None:
            """暂存当前提炼运行的派生记录，供当前单元持久化使用。"""
            nonlocal derivation
            derivation = value

        try:
            draft = await run_one_session(
                unit.session,
                date_str,
                root,
                review_source=review_source,
                host_factory=host_factory,
                derivation_sink=capture_derivation,
            )
        except DistillError as exc:
            logger.warning(
                "distill failed for %s (skipped, not advanced): %s",
                unit.label,
                exc,
            )
            continue

        activity = unit.activity()
        bad_dates = _out_of_range_dates(draft.diary, activity.dates)
        if bad_dates:
            logger.warning(
                "draft diary dates %s outside activity_dates %s for %s "
                "(skipped, not advanced)",
                bad_dates,
                activity.dates,
                unit.label,
            )
            continue

        audit = audit_draft(draft)
        if not audit.clean:
            logger.warning(
                "dualtrack leaks in %s: %s",
                unit.label,
                [(leak.date, leak.signal, leak.snippet) for leak in audit.leaks],
            )
        warnings = procedure_warnings(draft)
        if warnings:
            logger.warning("procedure gaps in %s: %s", unit.label, warnings)

        context = unit.build_context(activity, derivation)
        try:
            report = persist_draft(store, draft, context)
        except (OSError, ValueError) as exc:
            logger.warning(
                "persist failed for %s (skipped, not advanced): %s",
                unit.label,
                exc,
            )
            continue
        if not report.ok:
            logger.warning("persist incomplete for %s (not advanced)", unit.label)
            continue

        try:
            unit.advance()
        except ValueError as exc:
            logger.warning(
                "watermark failed for %s after persist: %s",
                unit.label,
                exc,
            )
            continue
        touched_dates.update(entry.date for entry in draft.diary)
        try:
            await judge_session(
                unit.session,
                date_str,
                root,
                host_factory=host_factory,
                segment_id=context.segment_id,
                review_source=review_source,
                activity_dates=activity.dates,
            )
        except Exception as exc:  # noqa: BLE001 - judge 是水位推进后的旁路步骤。
            logger.warning(
                "judge raised for %s (isolated; review unaffected): %s",
                unit.label,
                exc,
            )
    return touched_dates


def _out_of_range_dates(
    diary: tuple,
    activity_dates: tuple[str, ...],
) -> tuple[str, ...]:
    """返回草稿中不属于当前来源片段活动日期的日期。"""
    if not activity_dates:
        return tuple(entry.date for entry in diary)
    allowed = set(activity_dates)
    return tuple(entry.date for entry in diary if entry.date not in allowed)
