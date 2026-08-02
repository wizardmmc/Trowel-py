"""统一解析 refine 与 judge 实际可读取的 review 来源。"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from .models import JournalSlice, ReviewSource


class ReviewTargetUnavailable(ValueError):
    """表示本次必须处理的 journal 文件或字节区间已经不可用。"""


def _journal_slice_is_available(source: JournalSlice) -> bool:
    """检查 journal 文件存在且声明的字节区间仍落在当前文件内。"""
    path = Path(source.path)
    if not path.is_file():
        return False
    try:
        size = path.stat().st_size
    except OSError:
        return False
    return source.start_offset < size and (
        source.end_offset is None or source.end_offset <= size
    )


def resolve_available_review_source(
    source: ReviewSource,
) -> tuple[ReviewSource, int]:
    """拒绝不可读的 target，并移除不可读的历史 context。

    refine 与 judge 都必须通过本入口取得实际来源，避免两个阶段分别实现
    target 门禁和 context 降级。历史上下文只帮助理解，不能因旧文件丢失永久
    阻塞新目标；target 缺失时继续运行会造成错误提炼或判效，因此直接拒绝。

    Args:
        source: runtime 专用构造器生成的完整 review 来源。

    Returns:
        target 原样保留、context 只包含当前可读区间的来源定义，以及被移除的
        context 数量。

    Raises:
        ReviewTargetUnavailable: 任一 target 文件缺失或已短于声明范围。
    """
    unavailable_target = tuple(
        item for item in source.target if not _journal_slice_is_available(item)
    )
    if unavailable_target:
        raise ReviewTargetUnavailable(
            f"{len(unavailable_target)} review target source(s) unavailable"
        )
    available = replace(
        source,
        context=tuple(
            item for item in source.context if _journal_slice_is_available(item)
        ),
    )
    return available, len(source.context) - len(available.context)
