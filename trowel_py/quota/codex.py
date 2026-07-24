"""将 Codex 推送的 ``rate_limit_updated`` 事件折叠为统一额度快照。

``primary.resetsAt`` 来自真实录制，单位为秒；统一模型使用毫秒。translator
只转换外层字段，``primary`` 内仍须按 camelCase 读取。
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

# turn 结束后不再收到主动推送，旧快照必须标记为过期。
_TERMINAL_TYPES = frozenset({"finished", "interrupted", "error"})


def _as_float(value: Any) -> float | None:
    """接受有限数值，但拒绝 bool 和 NaN。"""

    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value) if value == value else None
    return None


def _now_ms() -> int:
    return int(time.time() * 1000)


def parse_codex_rate_limit(
    payload: Mapping[str, Any], *, account_id: str, fetched_at: int
) -> QuotaSnapshot:
    """缺少 ``primary`` 或有效 ``usedPercent`` 时生成 ``NO_DATA`` 快照。"""

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
        resets_ms = int(resets * 1000)  # 秒统一为毫秒。

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
    """构建同步 SessionHub observer，只折叠额度推送和 turn 终态。"""

    clock = now_ms or _now_ms

    def observe(envelope: Mapping[str, Any]) -> None:
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
