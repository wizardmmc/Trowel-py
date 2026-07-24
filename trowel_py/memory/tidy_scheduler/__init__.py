"""应用内 tidy scheduler 的稳定入口。"""

import logging

from trowel_py.memory.tidy_state import (
    MAX_PENDING_MONTHS,
    MAX_PENDING_WEEKS,
    advance_watermark,
    enumerate_pending_months,
    enumerate_pending_weeks,
    last_iso_week,
    last_month,
    load_state,
    next_iso_week,
    next_month,
    save_state,
)

from .explicit import run_explicit_catchup
from .report import _extract_failure as _extract_failure, tidy_succeeded
from .runtime import TidyScheduler
from .timing import seconds_until_next_monthday, seconds_until_next_weekday
from .types import (
    DEFAULT_MONTHLY_TIME,
    DEFAULT_WEEKLY_TIME,
    NowFn,
    ProviderFactory,
    Scope,
    SleepFn,
    TidyFn,
    _FIRST as _FIRST,
    _MONDAY as _MONDAY,
)

logger = logging.getLogger(__name__)

__all__ = [
    "DEFAULT_MONTHLY_TIME",
    "DEFAULT_WEEKLY_TIME",
    "MAX_PENDING_MONTHS",
    "MAX_PENDING_WEEKS",
    "NowFn",
    "ProviderFactory",
    "Scope",
    "SleepFn",
    "TidyFn",
    "TidyScheduler",
    "advance_watermark",
    "enumerate_pending_months",
    "enumerate_pending_weeks",
    "last_iso_week",
    "last_month",
    "load_state",
    "next_iso_week",
    "next_month",
    "run_explicit_catchup",
    "save_state",
    "seconds_until_next_monthday",
    "seconds_until_next_weekday",
    "tidy_succeeded",
]
