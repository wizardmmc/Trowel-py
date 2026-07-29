"""提供 WorkBroker 的预算合并与校验、槽 ID 生成、UTC 时间换算和用量校验函数。"""

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
    """逐轴合并策略与请求预算，生成不超过两者的上限。

    单轴为 None 表示该来源不限制该轴；两边都不限制时结果仍为 None。

    Args:
        policy_cap: broker 策略允许的预算上限。
        req_cap: 请求方提出的预算上限；None 表示不额外收窄策略。
        budget_type: 用合并后的四个预算轴构造结果的函数。
        min_fn: 两边都设置了上限时选取较小值的函数。

    Returns:
        由 budget_type 构造的合并预算。
    """

    def axis(policy_value: Any | None, request_value: Any | None) -> Any | None:
        """合并单个预算轴；两边都有上限时取较小值。"""

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
    """生成 provider、账号和账号内并发序号共同确定的槽 ID。

    Args:
        provider: 槽位所属的 provider；槽 ID 使用其 value。
        account: 槽位所属 provider 账号。
        idx: 账号内从 0 开始的并发槽序号。

    Returns:
        形如 ``provider:account:idx`` 的稳定槽 ID。
    """

    return f"{provider.value}:{account}:{idx}"


def parse_iso(
    value: str,
    *,
    fromisoformat: Callable[[str], Any],
    utc_resolver: Callable[[], Any],
) -> Any:
    """解析带时区的 ISO 时间并转换到 UTC。

    Args:
        value: 要解析的 ISO 时间；必须包含时区偏移。
        fromisoformat: 把 ISO 文本转换为时间对象的函数。
        utc_resolver: 在转换时返回 UTC 时区对象的函数。

    Returns:
        与 value 表示同一时刻的 UTC 时间对象。

    Raises:
        ValueError: value 不是有效的 ISO 时间，或没有时区偏移。
    """

    try:
        parsed = fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"not an ISO timestamp: {value!r}") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"timestamp lacks timezone offset: {value!r}")
    return parsed.astimezone(utc_resolver())


def utc_day(occurred_at: str, *, parse_iso: Callable[[str], Any]) -> str:
    """返回用量发生时刻所属的 UTC 日期。

    Args:
        occurred_at: 带时区的 ISO 用量发生时间。
        parse_iso: 解析 occurred_at 并转换到 UTC 的函数。

    Returns:
        ``YYYY-MM-DD`` 格式的 UTC 日期。
    """

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
    """拒绝类型错误、负数或非有限的预算上限。

    calls、tokens 和 wall_seconds 必须是非 bool 整数；cost 必须是非 bool
    的有限数值。各轴都允许 None，表示该轴不限。

    Args:
        cap: 要校验的四轴预算上限。
        isinstance_resolver: 返回当前运行时 isinstance 的函数。
        bool_type_resolver: 返回当前运行时 bool 类型的函数。
        int_type_resolver: 返回当前运行时 int 类型的函数。
        number_types_resolver: 返回 cost 接受的数值类型集合的函数。
        isfinite_resolver: 返回有限值判断函数的函数。

    Raises:
        ValueError: 任一已设置的预算轴类型错误、为负或不是有限值。
    """

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
    """校验用量时间，拒绝负计量值以及非有限或负费用。

    Args:
        usage: 要写入 WorkBroker 的一次用量观测。
        parse_iso_resolver: 返回当前 ISO 时间解析函数的函数。
        isfinite_resolver: 返回费用有限值判断函数的函数。

    Raises:
        ValueError: occurred_at 为空或不是带时区的 ISO 时间，任一计量值
            为负，或者 cost 非有限或为负。
    """

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
