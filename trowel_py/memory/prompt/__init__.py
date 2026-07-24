"""Memory prompt 的稳定入口。"""

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
    EPISODE_MAX_ITEMS_PER_DATE,
    EPISODE_MAX_ITEMS_PER_FIELD,
    EPISODE_MAX_ITEM_CHARS,
    EPISODE_MAX_TOTAL_CHARS,
    EPISODE_TARGET_ITEM_CHARS,
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
    """构造 refine prompt。"""
    return _build_refine_prompt(
        jsonl_path,
        cost_text,
        start_offset=start_offset,
        end_offset=end_offset,
        template=REFINE_PROMPT_TEMPLATE,
        episode_max_items=EPISODE_MAX_ITEMS_PER_DATE,
        episode_max_items_per_field=EPISODE_MAX_ITEMS_PER_FIELD,
        episode_target_item_chars=EPISODE_TARGET_ITEM_CHARS,
        episode_max_item_chars=EPISODE_MAX_ITEM_CHARS,
        episode_max_total_chars=EPISODE_MAX_TOTAL_CHARS,
    )


def build_daily_compress_prompt(*, date: str, sources_block: str) -> str:
    """构造 daily compression prompt。"""
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
    "EPISODE_MAX_ITEMS_PER_DATE",
    "EPISODE_MAX_ITEMS_PER_FIELD",
    "EPISODE_MAX_ITEM_CHARS",
    "EPISODE_MAX_TOTAL_CHARS",
    "EPISODE_TARGET_ITEM_CHARS",
    "NOTE_KINDS",
    "REFINE_PROMPT_TEMPLATE",
    "VERIFICATION_TIERS",
    "build_daily_compress_prompt",
    "build_refine_prompt",
]
