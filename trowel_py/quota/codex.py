"""把 SessionHub 分发的 Codex 额度事件同步到统一额度读模型。

Codex 原生通知中的 ``primary.resetsAt`` 是 Unix 秒级时间戳，统一模型的
``QuotaWindow.resets_at`` 则是 Unix 毫秒时间戳。事件翻译层只转换外层字段；
``primary`` 内的字段仍保留 Codex 的 camelCase 名称。
"""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping
from typing import Any

from trowel_py.quota.read_model import QuotaReadModel
from trowel_py.quota.types import (
    Provider,
    QuotaSnapshot,
    QuotaStatus,
    QuotaWindow,
    QuotaWindowKind,
)

DEFAULT_CODEX_ACCOUNT_ID = "codex"

# SessionHub 的这三类事件都表示当前 turn 已结束，额度快照随之失效。
_TERMINAL_TYPES = frozenset({"finished", "interrupted", "error"})


def _as_float(value: Any) -> float | None:
    """把 int 或 float 转为 float，但拒绝 bool 和 NaN。"""

    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value) if value == value else None
    return None


def _now_ms() -> int:
    """返回当前 Unix 时间戳，单位为毫秒。"""

    return int(time.time() * 1000)


def parse_codex_rate_limit(
    payload: Mapping[str, Any], *, account_id: str, fetched_at: int
) -> QuotaSnapshot:
    """把 Codex 额度事件中的 payload 转换为统一额度快照。

    ``primary`` 不是映射，或 ``usedPercent`` 不是 ``int`` 或 ``float``、
    为 ``bool`` 或 NaN 时，返回不含窗口的 ``NO_DATA`` 快照。

    Args:
        payload: ``rate_limit_updated`` 事件的额度数据；外层字段已转换为
            snake_case，``primary`` 内仍保留 Codex 的 camelCase 字段名。
        account_id: 记录在快照中的 Codex 账号 ID；读模型用它区分账号。
        fetched_at: 事件处理时的 Unix 时间戳，单位为毫秒。
    """

    primary = payload.get("primary")
    if not isinstance(primary, Mapping):
        return QuotaSnapshot(
            provider=Provider.CODEX,
            account_id=account_id,
            plan_level=None,
            windows=(),
            fetched_at=fetched_at,
            status=QuotaStatus.NO_DATA,
        )

    used = _as_float(primary.get("usedPercent"))
    if used is None:
        return QuotaSnapshot(
            provider=Provider.CODEX,
            account_id=account_id,
            plan_level=None,
            windows=(),
            fetched_at=fetched_at,
            status=QuotaStatus.NO_DATA,
        )

    resets = primary.get("resetsAt")
    resets_ms: int | None = None
    if isinstance(resets, (int, float)) and not isinstance(resets, bool):
        resets_ms = int(resets * 1000)

    plan_type = payload.get("plan_type")
    plan_level = plan_type if isinstance(plan_type, str) and plan_type else None

    window = QuotaWindow(
        kind=QuotaWindowKind.RATE_LIMIT,
        used_percent=used,
        resets_at=resets_ms,
        raw=dict(primary),
    )
    return QuotaSnapshot(
        provider=Provider.CODEX,
        account_id=account_id,
        plan_level=plan_level,
        windows=(window,),
        fetched_at=fetched_at,
        status=QuotaStatus.OK,
    )


def make_codex_observer(
    read_model: QuotaReadModel,
    *,
    account_id: str = DEFAULT_CODEX_ACCOUNT_ID,
    now_ms: Callable[[], int] | None = None,
) -> Callable[[Mapping[str, Any]], None]:
    """构建把 SessionHub 事件同步到额度读模型的处理函数。

    该函数用 ``rate_limit_updated`` 更新快照；收到 ``finished``、
    ``interrupted`` 或 ``error`` 时，将已有正常快照标记为 ``STALE``。

    Args:
        read_model: 保存各账号最新额度的进程内读模型。
        account_id: 写入或标记过期时使用的 Codex 账号 ID。
        now_ms: 创建快照时写入 ``fetched_at`` 的 Unix 毫秒时钟；为 None 时
            读取系统时间。
    """

    clock = now_ms or _now_ms

    def observe(envelope: Mapping[str, Any]) -> None:
        """根据一条 SessionHub 事件更新 Codex 额度快照，或把它标记为过期。

        Args:
            envelope: SessionHub 分发的统一格式会话事件。
        """

        if not isinstance(envelope, Mapping):
            return
        etype = envelope.get("type")
        if etype == "rate_limit_updated":
            data = envelope.get("payload")
            if isinstance(data, Mapping):
                snapshot = parse_codex_rate_limit(
                    data, account_id=account_id, fetched_at=int(clock())
                )
                read_model.update(snapshot)
        elif etype in _TERMINAL_TYPES:
            read_model.mark_stale(Provider.CODEX, account_id)

    return observe
