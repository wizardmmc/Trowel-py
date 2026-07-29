"""WorkBroker usage 的事务内持久化操作；事务与锁由调用方持有。"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from typing import Any, TypeVar

_Totals = TypeVar("_Totals")


def observation_seen(
    connection: sqlite3.Connection,
    *,
    lease_id: str,
    observation_id: str,
) -> bool:
    """判断同一 lease 是否已记录指定 observation_id。"""

    row = connection.execute(
        "SELECT 1 FROM work_usage WHERE lease_id=? AND observation_id=?",
        (lease_id, observation_id),
    ).fetchone()
    return row is not None


def mark_lease_started(connection: sqlite3.Connection, *, lease_id: str) -> None:
    """将 lease 标记为已开始。

    已开始的 default lease 不再被 foreground 抢占。

    Args:
        connection: WorkBroker 已打开的 SQLite 连接。
        lease_id: 要标记的 lease ID。
    """

    connection.execute(
        "UPDATE work_leases SET started=1 WHERE lease_id=?",
        (lease_id,),
    )


def insert_usage(
    connection: sqlite3.Connection,
    *,
    lease_id: str,
    lease_row: sqlite3.Row,
    usage: Any,
    day: str,
    policy_version: str,
) -> None:
    """写入一次用量，并从 lease 行复制业务归因维度。

    本函数不校验 lease 状态、fencing token 或用量值；上层入口负责应用各自的
    校验策略。lease_id 和 policy_version 由独立参数提供；调用方必须确保
    lease_id 与 lease_row 属于同一 lease。provider、账号、工作类别、档位、
    Task 和 WorkItem 均取自 lease_row，不接受 usage 覆盖。

    Args:
        connection: WorkBroker 已打开的 SQLite 连接。
        lease_id: 用量所属的 lease ID。
        lease_row: 提供可信归因字段的完整 work_leases 行。
        usage: 提供观测 ID、计量值和发生时间的用量记录。
        day: 用量发生时刻所属的 UTC 日期。
        policy_version: 记账时生效并随记录保存的 broker 策略版本。
    """

    connection.execute(
        "INSERT INTO work_usage (observation_id, lease_id, provider, "
        "account_id, work_kind, model_tier, task_id, work_item_id, calls, "
        "input_tokens, output_tokens, cost, wall_seconds, occurred_at, "
        "day, policy_version) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            usage.observation_id,
            lease_id,
            lease_row["provider"],
            lease_row["account_id"],
            lease_row["work_kind"],
            lease_row["model_tier"],
            lease_row["task_id"],
            lease_row["work_item_id"],
            usage.calls,
            usage.input_tokens,
            usage.output_tokens,
            usage.cost,
            usage.wall_seconds,
            usage.occurred_at,
            day,
            policy_version,
        ),
    )


def totals_in_tx(
    connection: sqlite3.Connection,
    *,
    day: str | None = None,
    work_kind: Any = None,
    provider: Any = None,
    account_id: str | None = None,
    task_id: str | None = None,
    model_tier: Any = None,
    totals_factory: Callable[..., _Totals],
) -> _Totals:
    """按全部指定的范围条件聚合用量，并保留未知费用。

    没有记录时各计数与费用均为 0。任一匹配记录的 cost 为 NULL 时，汇总
    cost 为 None；未报告的 wall_seconds 则不计入总和。

    Args:
        connection: WorkBroker 已打开的 SQLite 连接。
        day: UTC 日期筛选；None 表示不按日期筛选。
        work_kind: 工作类别筛选；None 表示不按工作类别筛选。
        provider: 模型 provider 筛选；None 表示不按 provider 筛选。
        account_id: provider 账号筛选；None 表示不按账号筛选。
        task_id: Task 归属筛选；None 表示不按 Task 筛选。
        model_tier: 请求方预估档位筛选；None 表示不按档位筛选。
        totals_factory: 接受 calls、input_tokens、output_tokens、cost 和
            wall_seconds 五个关键字参数的构造函数。

    Returns:
        由 totals_factory 构造的聚合用量。
    """

    clauses: list[str] = []
    params: list[Any] = []
    if day is not None:
        clauses.append("day=?")
        params.append(day)
    if work_kind is not None:
        clauses.append("work_kind=?")
        params.append(work_kind.value)
    if provider is not None:
        clauses.append("provider=?")
        params.append(provider.value)
    if account_id is not None:
        clauses.append("account_id=?")
        params.append(account_id)
    if task_id is not None:
        clauses.append("task_id=?")
        params.append(task_id)
    if model_tier is not None:
        clauses.append("model_tier=?")
        params.append(model_tier.value)
    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    row = connection.execute(
        "SELECT COALESCE(SUM(calls),0) AS calls, "
        "COALESCE(SUM(input_tokens),0) AS input_tokens, "
        "COALESCE(SUM(output_tokens),0) AS output_tokens, "
        "CASE WHEN COUNT(*)=0 THEN 0 "
        "WHEN COUNT(cost)=COUNT(*) THEN SUM(cost) ELSE NULL END AS cost, "
        "COALESCE(SUM(wall_seconds),0) AS wall_seconds "
        f"FROM work_usage{where}",
        params,
    ).fetchone()
    cost_raw = row["cost"]
    return totals_factory(
        calls=int(row["calls"]),
        input_tokens=int(row["input_tokens"]),
        output_tokens=int(row["output_tokens"]),
        cost=float(cost_raw) if cost_raw is not None else None,
        wall_seconds=int(row["wall_seconds"]),
    )
