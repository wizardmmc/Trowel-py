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
    jsonl_path: str,
    cost_text: str,
    *,
    start_offset: int | None = None,
    end_offset: int | None = None,
) -> str:
    """用当前 refine 模板构造会话提炼 prompt。

    底层实现先全局替换 ``{jsonl_path}``，再替换 ``{cost}``，因此路径文本
    中的成本占位符也会被第二步替换。任一 offset 非 ``None`` 时添加增量
    范围：起点 ``None`` 或 0 都写为 0，终点 ``None`` 写为 ``EOF``，0 和
    负值原样保留。本函数不读取 JSONL，也不校验路径、offset 顺序、文件边界
    或成本文本。

    Args:
        jsonl_path: 注入模板的原始 JSONL 路径文本。
        cost_text: 注入模板的客观成本文本。
        start_offset: 可选的原 JSONL 起始字节偏移。
        end_offset: 可选的原 JSONL 结束字节偏移。

    Returns:
        使用调用时 ``REFINE_PROMPT_TEMPLATE`` 构造的完整 prompt。
    """
    return _build_refine_prompt(
        jsonl_path,
        cost_text,
        start_offset=start_offset,
        end_offset=end_offset,
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
