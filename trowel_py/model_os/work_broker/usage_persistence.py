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
    row = connection.execute(
        "SELECT 1 FROM work_usage WHERE lease_id=? AND observation_id=?",
        (lease_id, observation_id),
    ).fetchone()
    return row is not None


def mark_lease_started(connection: sqlite3.Connection, *, lease_id: str) -> None:
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
