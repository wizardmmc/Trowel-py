"""定义各模型服务商共用的额度状态、窗口和快照。"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping


class Provider(str, Enum):
    """标识额度所属的模型服务商。"""

    GLM = "glm"
    CODEX = "codex"


class QuotaStatus(str, Enum):
    """表示额度快照是否可用于判断剩余额度。

    WorkBroker 把非 ``OK`` 状态视为额度未知，不会单凭该状态拒绝工作请求。
    """

    OK = "ok"
    NO_DATA = "no-data"
    AUTH_ERROR = "auth-error"
    SERVER_ERROR = "server-error"
    NETWORK_ERROR = "network-error"
    STALE = "stale"


class QuotaWindowKind(str, Enum):
    """标识额度窗口的统计范围或用量类别。

    不同服务商不要求提供相同的窗口种类。
    """

    SESSION_5H = "session_5h"
    WEEKLY = "weekly"
    RATE_LIMIT = "rate_limit"
    WEB_SEARCHES_MONTHLY = "web_searches_monthly"


@dataclass(frozen=True)
class QuotaWindow:
    """记录一个额度窗口的统一用量和服务商原始字段。

    Attributes:
        kind: 该窗口统计的时间范围或用量类别。
        used_percent: 该窗口已经使用的额度百分比。
        resets_at: 窗口下次重置的 Unix 毫秒时间戳；服务商未提供或字段无法解析时
            为 None。
        raw: 服务商返回的窗口对象副本，供内部诊断；额度 HTTP 响应不包含此字段。
    """

    kind: QuotaWindowKind
    used_percent: float
    resets_at: int | None
    raw: Mapping[str, Any]


@dataclass(frozen=True)
class QuotaSnapshot:
    """记录一次服务商账号额度读取的统一结果。

    Attributes:
        provider: 提供额度数据的模型服务商。
        account_id: Trowel 用来区分额度账号的本地 ID，与 provider 一起标识读模型
            中的账号。
        plan_level: 服务商报告的套餐级别；无法取得时为 None。
        windows: 本次取得的额度窗口；没有可用窗口时为空元组。
        fetched_at: 发起额度读取或处理额度事件时的 Unix 毫秒时间戳，读模型用它
            判断快照是否过期。
        status: 本次额度数据是否可用于判断剩余额度。
    """

    provider: Provider
    account_id: str
    plan_level: str | None
    windows: tuple[QuotaWindow, ...]
    fetched_at: int
    status: QuotaStatus
