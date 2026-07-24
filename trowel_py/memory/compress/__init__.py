"""日记压缩的稳定入口。"""

import logging

from .daily import (
    _existing_daily_ok as _existing_daily_ok,
    _existing_daily_usable as _existing_daily_usable,
    _write_daily as _write_daily,
    _write_fallback_body as _write_fallback_body,
    compress_daily,
    daily_dates_needing_rebuild,
    write_fallback_daily,
)
from .daily_generation import (
    _DAILY_BUDGET as _DAILY_BUDGET,
    _DAILY_GENERATION_VERSION as _DAILY_GENERATION_VERSION,
    _SECTION_FOR_TYPE as _SECTION_FOR_TYPE,
    _SECTION_ORDER as _SECTION_ORDER,
    _TYPE_PRIORITY as _TYPE_PRIORITY,
    _DailyItem as _DailyItem,
    _dedup_items as _dedup_items,
    _generate_items as _generate_items,
    _normalize as _normalize,
    _parse_and_validate as _parse_and_validate,
    _render_daily_body as _render_daily_body,
    _render_sources_block as _render_sources_block,
    _required_sections as _required_sections,
    _select_within_budget as _select_within_budget,
    _source_aliases as _source_aliases,
    _source_hash as _source_hash,
)
from .rollup import (
    BYPASS_CATEGORIES,
    _INPUT_CAP as _INPUT_CAP,
    _MONTHLY_SYS as _MONTHLY_SYS,
    _MONTHLY_USER as _MONTHLY_USER,
    _OUTPUT_CAP as _OUTPUT_CAP,
    _WEEKLY_SYS as _WEEKLY_SYS,
    _WEEKLY_USER as _WEEKLY_USER,
    _cap as _cap,
    _in_iso_week as _in_iso_week,
    _parse_iso_week as _parse_iso_week,
    _parse_weekly_output as _parse_weekly_output,
    _week_in_month as _week_in_month,
    _write_bypass as _write_bypass,
    compress_monthly,
    compress_weekly,
)

logger = logging.getLogger(__name__)

__all__ = [
    "BYPASS_CATEGORIES",
    "compress_daily",
    "compress_monthly",
    "compress_weekly",
    "daily_dates_needing_rebuild",
    "write_fallback_daily",
]
