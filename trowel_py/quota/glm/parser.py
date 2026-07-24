"""GLM quota payload 的纯解析。"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any


def as_float(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value) if value == value else None
    if isinstance(value, str) and value.strip():
        try:
            return float(value)
        except ValueError:
            return None
    return None


def as_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return None


def find_limit(
    limits: list[Mapping[str, Any]],
    type_: str,
    unit: int | None,
) -> Mapping[str, Any] | None:
    """优先精确 unit，再退回第一个未声明 unit 的同类窗口。"""
    fallback: Mapping[str, Any] | None = None
    for item in limits:
        if item.get("type") != type_:
            continue
        item_unit = item.get("unit")
        if unit is None:
            return item
        if item_unit == unit:
            return item
        if fallback is None and item_unit is None:
            fallback = item
    return fallback


def window(
    kind: Any,
    limit: Mapping[str, Any] | None,
    *,
    as_float: Callable[[Any], float | None],
    as_int: Callable[[Any], int | None],
    window_type: Callable[..., Any],
    monthly_kind: Any,
) -> Any | None:
    if limit is None:
        return None
    used = as_float(limit.get("percentage"))
    if used is None and kind is monthly_kind:
        used_value = as_float(limit.get("currentValue"))
        capacity = as_float(limit.get("usage"))
        if used_value is not None and capacity:
            used = used_value / capacity * 100.0
    if used is None:
        return None
    return window_type(
        kind=kind,
        used_percent=used,
        resets_at=as_int(limit.get("nextResetTime")),
        raw=dict(limit),
    )


def extract_limits(
    raw: Mapping[str, Any],
    *,
    mapping_type: type[Any],
) -> tuple[list[Mapping[str, Any]], Mapping[str, Any]]:
    data = raw.get("data")
    container: Mapping[str, Any] = (
        data
        if isinstance(data, mapping_type)
        else raw
        if isinstance(raw, mapping_type)
        else {}
    )
    maybe = container.get("limits") if isinstance(container, mapping_type) else None
    if isinstance(maybe, list):
        return [item for item in maybe if isinstance(item, mapping_type)], container
    if isinstance(container, list):
        return list(container), {}
    return [], container


def parse_quota(
    raw: Mapping[str, Any],
    *,
    account_id: str,
    fetched_at: int,
    extract_limits: Callable[
        [Mapping[str, Any]],
        tuple[list[Mapping[str, Any]], Mapping[str, Any]],
    ],
    find_limit: Callable[
        [list[Mapping[str, Any]], str, int | None],
        Mapping[str, Any] | None,
    ],
    build_window: Callable[[Any, Mapping[str, Any] | None], Any | None],
    snapshot_without_windows: Callable[[str, int, Any], Any],
    snapshot_type: Callable[..., Any],
    provider: Any,
    ok_status: Any,
    no_data_status: Any,
    session_kind: Any,
    weekly_kind: Any,
    monthly_kind: Any,
    session_unit: int,
    weekly_unit: int,
    mapping_type: type[Any],
) -> Any:
    limits, container = extract_limits(raw)
    if not limits:
        return snapshot_without_windows(
            account_id,
            fetched_at,
            no_data_status,
        )

    windows = []
    for kind, type_, unit in (
        (session_kind, "TOKENS_LIMIT", session_unit),
        (weekly_kind, "TOKENS_LIMIT", weekly_unit),
        (monthly_kind, "TIME_LIMIT", None),
    ):
        parsed = build_window(kind, find_limit(limits, type_, unit))
        if parsed is not None:
            windows.append(parsed)

    level = container.get("level") if isinstance(container, mapping_type) else None
    plan_level = level if isinstance(level, str) and level else None
    status = ok_status if windows else no_data_status
    return snapshot_type(
        provider=provider,
        account_id=account_id,
        plan_level=plan_level,
        windows=tuple(windows),
        fetched_at=fetched_at,
        status=status,
    )
