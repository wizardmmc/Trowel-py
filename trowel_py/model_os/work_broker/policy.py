"""WorkBroker 的确定性预算、时间与输入校验策略。"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any


def narrow_cap(
    policy_cap: Any,
    req_cap: Any | None,
    *,
    budget_type: Callable[..., Any],
    min_fn: Callable[[Any, Any], Any],
) -> Any:
    def axis(policy_value: Any | None, request_value: Any | None) -> Any | None:
        if policy_value is None and request_value is None:
            return None
        if policy_value is None:
            return request_value
        if request_value is None:
            return policy_value
        return min_fn(policy_value, request_value)

    return budget_type(
        calls=axis(policy_cap.calls, req_cap.calls if req_cap else None),
        tokens=axis(policy_cap.tokens, req_cap.tokens if req_cap else None),
        cost=axis(policy_cap.cost, req_cap.cost if req_cap else None),
        wall_seconds=axis(
            policy_cap.wall_seconds,
            req_cap.wall_seconds if req_cap else None,
        ),
    )


def slot_id(provider: Any, account: str, idx: int) -> str:
    return f"{provider.value}:{account}:{idx}"


def parse_iso(
    value: str,
    *,
    fromisoformat: Callable[[str], Any],
    utc_resolver: Callable[[], Any],
) -> Any:
    try:
        parsed = fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"not an ISO timestamp: {value!r}") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"timestamp lacks timezone offset: {value!r}")
    return parsed.astimezone(utc_resolver())


def utc_day(occurred_at: str, *, parse_iso: Callable[[str], Any]) -> str:
    return parse_iso(occurred_at).date().isoformat()


def validate_cap(
    cap: Any,
    *,
    isinstance_resolver: Callable[[], Callable[[Any, Any], bool]],
    bool_type_resolver: Callable[[], Any],
    int_type_resolver: Callable[[], Any],
    number_types_resolver: Callable[[], Any],
    isfinite_resolver: Callable[[], Callable[[Any], bool]],
) -> None:
    for label, value in (
        ("calls", cap.calls),
        ("tokens", cap.tokens),
        ("wall_seconds", cap.wall_seconds),
    ):
        if value is None:
            continue
        if isinstance_resolver()(value, bool_type_resolver()) or not (
            isinstance_resolver()(value, int_type_resolver())
        ):
            raise ValueError(f"BudgetDimensions.{label} must be an int, got {value!r}")
        if value < 0:
            raise ValueError(f"BudgetDimensions.{label} must be non-negative")
    if cap.cost is not None and (
        isinstance_resolver()(cap.cost, bool_type_resolver())
        or not isinstance_resolver()(cap.cost, number_types_resolver())
        or not isfinite_resolver()(cap.cost)
        or cap.cost < 0
    ):
        raise ValueError("BudgetDimensions.cost must be a finite, non-negative number")


def validate_usage(
    usage: Any,
    *,
    parse_iso_resolver: Callable[[], Callable[[str], Any]],
    isfinite_resolver: Callable[[], Callable[[Any], bool]],
) -> None:
    if not usage.occurred_at:
        raise ValueError("UsageRecord.occurred_at must be a non-empty ISO timestamp")
    parse_iso = parse_iso_resolver()
    parse_iso(usage.occurred_at)
    for label, value in (
        ("calls", usage.calls),
        ("input_tokens", usage.input_tokens),
        ("output_tokens", usage.output_tokens),
    ):
        if value < 0:
            raise ValueError(f"UsageRecord.{label} must be non-negative")
    if usage.wall_seconds is not None and usage.wall_seconds < 0:
        raise ValueError("UsageRecord.wall_seconds must be non-negative")
    if usage.cost is not None and (
        not isfinite_resolver()(usage.cost) or usage.cost < 0
    ):
        raise ValueError("UsageRecord.cost must be finite and non-negative")
