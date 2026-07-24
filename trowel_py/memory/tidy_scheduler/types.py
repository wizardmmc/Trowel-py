"""Tidy scheduler 的类型与默认时间。"""

from __future__ import annotations

from datetime import datetime, time
from typing import Any, Awaitable, Callable, Literal

from trowel_py.llm.client import LLMProvider

DEFAULT_WEEKLY_TIME: time = time(3, 30)
DEFAULT_MONTHLY_TIME: time = time(4, 0)

_MONDAY: int = 0
_FIRST: int = 1

NowFn = Callable[[], datetime]
SleepFn = Callable[[float], Awaitable[None]]
TidyFn = Callable[[str], Any]
ProviderFactory = Callable[[], LLMProvider]
Scope = Literal["weekly", "monthly"]
