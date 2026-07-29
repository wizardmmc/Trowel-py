"""WorkBroker 租约持久化字段的无 I/O 转换。"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from typing import Any, Protocol, TypeVar


class _BudgetCap(Protocol):
    """声明预算上限编码所需的四个计量轴。"""

    @property
    def calls(self) -> int | None:
        """返回调用次数上限；None 表示不限次数。"""

        ...

    @property
    def tokens(self) -> int | None:
        """返回输入与输出 token 总数上限；None 表示不限 token。"""

        ...

    @property
    def cost(self) -> float | None:
        """返回 provider 报告的费用上限；None 表示不限费用。"""

        ...

    @property
    def wall_seconds(self) -> int | None:
        """返回累计墙钟时间的秒数上限；None 表示不限时间。"""

        ...


_Budget = TypeVar("_Budget")
_Lease = TypeVar("_Lease")


def cap_to_json(
    cap: _BudgetCap | None,
    *,
    dumps: Callable[[dict[str, Any]], str],
) -> str | None:
    """把可选预算上限编码为 lease 表的 granted_cap 字段。

    Args:
        cap: 要编码的预算上限；None 表示 broker 不设内部预算。
        dumps: 把四个预算轴编码为 JSON 的函数。

    Returns:
        编码后的 JSON；cap 为 None 时不调用 dumps 并直接返回 None。
    """

    if cap is None:
        return None
    return dumps(
        {
            "calls": cap.calls,
            "tokens": cap.tokens,
            "cost": cap.cost,
            "wall_seconds": cap.wall_seconds,
        }
    )


def row_to_lease(
    row: sqlite3.Row,
    *,
    loads: Callable[[str], dict[str, Any]],
    budget_dimensions_type: Callable[..., _Budget],
    work_lease_type: Callable[..., _Lease],
    provider_type: Callable[[Any], Any],
    work_kind_type: Callable[[Any], Any],
    model_tier_type: Callable[[Any], Any],
) -> _Lease:
    """把 WorkBroker 的 SQLite 行转换为调用方指定的 lease 对象。

    granted_cap 为 None、空字符串或 0 时会还原为 None；JSON 中缺少的预算轴
    会以 None 传给 budget_dimensions_type。解析 JSON、转换枚举或整数，或者
    调用构造器失败时，异常原样向上传递。

    Args:
        row: 包含完整 WorkBroker lease 列的 SQLite 行。
        loads: 解析 granted_cap JSON 的函数。
        budget_dimensions_type: 用四个可选计量轴构造预算上限的函数。
        work_lease_type: 用持久化字段构造 lease 的函数。
        provider_type: 把 provider 字段转换为公开类型的函数。
        work_kind_type: 把 work_kind 字段转换为公开类型的函数。
        model_tier_type: 把 model_tier 字段转换为公开类型的函数。

    Returns:
        由 work_lease_type 构造的 lease 对象。
    """

    cap_json = row["granted_cap"]
    granted_cap = None
    if cap_json:
        data = loads(cap_json)
        granted_cap = budget_dimensions_type(
            calls=data.get("calls"),
            tokens=data.get("tokens"),
            cost=data.get("cost"),
            wall_seconds=data.get("wall_seconds"),
        )
    return work_lease_type(
        lease_id=row["lease_id"],
        slot=row["slot"],
        provider=provider_type(row["provider"]),
        account_id=row["account_id"],
        work_kind=work_kind_type(row["work_kind"]),
        model_tier=model_tier_type(row["model_tier"]),
        granted_cap=granted_cap,
        acquired_at=row["acquired_at"],
        expires_at=row["expires_at"],
        fencing_token=int(row["fencing_token"]),
        task_id=row["task_id"],
        work_item_id=row["work_item_id"],
    )
