"""统一导出 Memory refine 与 Daily compression 的 prompt 契约。

两个公开构造函数保留在本模块，并在每次调用时把本模块当前模板传给内部实现，
因此运行时替换模板会立即生效。
"""

from __future__ import annotations

from .daily import (
    DAILY_COMPRESS_TEMPLATE,
    DAILY_ITEMS_SCHEMA,
    DAILY_ITEM_TYPES,
)
from .daily import build_daily_compress_prompt as _build_daily_compress_prompt
from .refine import (
    DRAFT_SCHEMA,
    DUALTRACK_SIGNAL_WORDS,
    NOTE_KINDS,
    REFINE_PROMPT_TEMPLATE,
    VERIFICATION_TIERS,
)
from .refine import build_refine_prompt as _build_refine_prompt


def build_refine_prompt(
    review_source: str,
    cost_text: str,
) -> str:
    """用当前 refine 模板构造带明确上下文和目标边界的提炼 prompt。

    底层实现先全局替换 ``{review_source}``，再替换 ``{cost}``，因此来源文本
    中的成本占位符也会被第二步替换。本函数不读取 journal，也不校验来源文本
    或成本文本。

    Args:
        review_source: 已明确区分历史上下文和本次处理目标的路径与范围说明。
        cost_text: 注入模板的客观成本文本。

    Returns:
        使用调用时 ``REFINE_PROMPT_TEMPLATE`` 构造的完整 prompt。
    """
    return _build_refine_prompt(
        review_source,
        cost_text,
        template=REFINE_PROMPT_TEMPLATE,
    )


def build_daily_compress_prompt(*, date: str, sources_block: str) -> str:
    """用当前 Daily compression 模板构造日期摘要 prompt。

    底层实现先全局替换 ``{date}``，再替换 ``{sources_block}``；日期文本中
    注入的来源占位符会继续被第二步替换，来源块中的日期占位符不会回头替换。
    函数不校验日期、来源 alias 或模板是否仍含未替换占位符。

    Args:
        date: 注入模板的目标日期文本。
        sources_block: 注入模板的结构化 segment 来源块。

    Returns:
        使用调用时 ``DAILY_COMPRESS_TEMPLATE`` 构造的完整 prompt。
    """
    return _build_daily_compress_prompt(
        date=date,
        sources_block=sources_block,
        template=DAILY_COMPRESS_TEMPLATE,
    )


__all__ = [
    "DAILY_COMPRESS_TEMPLATE",
    "DAILY_ITEMS_SCHEMA",
    "DAILY_ITEM_TYPES",
    "DRAFT_SCHEMA",
    "DUALTRACK_SIGNAL_WORDS",
    "NOTE_KINDS",
    "REFINE_PROMPT_TEMPLATE",
    "VERIFICATION_TIERS",
    "build_daily_compress_prompt",
    "build_refine_prompt",
]
