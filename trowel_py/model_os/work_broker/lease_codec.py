"""WorkBroker 租约持久化字段的无 I/O 转换。"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from typing import Any, Protocol, TypeVar


class _BudgetCap(Protocol):
    @property
    def calls(self) -> int | None: ...

    @property
    def tokens(self) -> int | None: ...

    @property
    def cost(self) -> float | None: ...

    @property
    def wall_seconds(self) -> int | None: ...


_Budget = TypeVar("_Budget")
_Lease = TypeVar("_Lease")


def cap_to_json(
    cap: _BudgetCap | None,
    *,
    dumps: Callable[[dict[str, Any]], str],
) -> str | None:
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
