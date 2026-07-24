"""画像建议记录的 JSON codec。"""

from __future__ import annotations

from collections.abc import Callable, Collection
from typing import Any

from trowel_py.memory.types import Suggestion


def suggestion_from_dict(
    item: dict[str, object],
    *,
    valid_dimensions: Collection[str],
    valid_statuses: Collection[str],
    suggestion_type: type[Suggestion],
    cast_value: Callable[[type[Any], object], Any],
    dimension_type: type[Any],
    status_type: type[Any],
) -> Suggestion:
    """解析一条记录，并拒绝未知维度与状态。"""
    dimension = item.get("dimension")
    status = item.get("status", "pending")
    if dimension not in valid_dimensions:
        raise ValueError(f"unknown dimension {dimension!r} in suggestion queue")
    if status not in valid_statuses:
        raise ValueError(f"unknown status {status!r} in suggestion queue")
    if not item.get("id"):
        raise ValueError("suggestion missing id in queue")

    sources = item.get("sources", [])
    raw_policy_version = item.get("policy_version", 1)
    if isinstance(raw_policy_version, bool):
        policy_version = 1
    elif isinstance(raw_policy_version, int):
        policy_version = raw_policy_version
    else:
        try:
            policy_version = int(str(raw_policy_version))
        except (TypeError, ValueError):
            policy_version = 1

    return suggestion_type(
        id=str(item["id"]),
        dimension=cast_value(dimension_type, dimension),
        body=str(item.get("body") or ""),
        sources=(
            tuple(str(source) for source in sources)
            if isinstance(sources, list)
            else ()
        ),
        date=str(item.get("date", "")),
        status=cast_value(status_type, status),
        policy_version=policy_version,
    )


def suggestion_to_dict(suggestion: Suggestion) -> dict[str, object]:
    """按稳定字段顺序编码一条建议。"""
    return {
        "id": suggestion.id,
        "dimension": suggestion.dimension,
        "body": suggestion.body,
        "sources": list(suggestion.sources),
        "date": suggestion.date,
        "status": suggestion.status,
        "policy_version": suggestion.policy_version,
    }
