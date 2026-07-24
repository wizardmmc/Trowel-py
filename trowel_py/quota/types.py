"""跨 provider quota 只读模型的冻结值对象。"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping


class Provider(str, Enum):
    GLM = "glm"
    CODEX = "codex"


class QuotaStatus(str, Enum):
    """额度读取失败只降级为非正常状态，不能阻断 episode。"""

    OK = "ok"
    NO_DATA = "no-data"
    AUTH_ERROR = "auth-error"
    SERVER_ERROR = "server-error"
    NETWORK_ERROR = "network-error"
    STALE = "stale"


class QuotaWindowKind(str, Enum):
    """不同 provider 的窗口不要求一一对应，统一比较 ``used_percent``。"""

    SESSION_5H = "session_5h"
    WEEKLY = "weekly"
    RATE_LIMIT = "rate_limit"
    WEB_SEARCHES_MONTHLY = "web_searches_monthly"


@dataclass(frozen=True)
class QuotaWindow:
    """``used_percent`` 统一表示已用比例，重置时间统一为 epoch 毫秒。"""

    kind: QuotaWindowKind
    used_percent: float
    resets_at: int | None
    raw: Mapping[str, Any]


@dataclass(frozen=True)
class QuotaSnapshot:
    provider: Provider
    account_id: str
    plan_level: str | None
    windows: tuple[QuotaWindow, ...]
    fetched_at: int
    status: QuotaStatus
