"""提供不执行 I/O 的 GLM 额度响应解析函数。"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any, TypeVar


_SnapshotT = TypeVar("_SnapshotT")


def as_float(value: Any) -> float | None:
    """把 int、float 或非空数值字符串转换为 float。

    bool、float 类型的 NaN、无法解析的字符串和其他类型返回 None；float 类型的
    正负无穷，以及字符串 ``"nan"``、``"inf"``、``"-inf"`` 会保留为相应的
    非有限浮点值。
    """

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
    """把 int 或没有小数部分的 float 转换为 int，拒绝 bool 和字符串。"""

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
    """按 ``type`` 和 ``unit`` 查找额度项。

    指定 ``unit`` 时优先精确匹配，找不到时退回同类型且没有 ``unit`` 的第一项；
    ``unit`` 为 None 时返回第一项同类型额度。
    """

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
    """把一个 GLM 原始额度项转换为统一额度窗口。

    优先把 ``percentage`` 转换为已用百分比；只有月度搜索窗口缺少可转换的
    ``percentage`` 时，才按 ``currentValue / usage * 100`` 计算，``usage`` 为 0
    或无法转换时不计算。没有可用百分比时返回 None。结果不校验是否有限，也不
    限制在 0 到 100；窗口的 ``raw`` 保存原额度项的浅拷贝。

    Args:
        kind: 生成窗口时写入的统一窗口类别。
        limit: GLM 返回的原始额度项；为 None 时不生成窗口。
        as_float: 读取百分比、当前用量和总量的数值转换函数。
        as_int: 读取 ``nextResetTime`` 的整数转换函数。
        window_type: 创建统一额度窗口的构造函数。
        monthly_kind: 允许通过当前用量和总量计算百分比的月度窗口类别。

    Returns:
        转换后的统一额度窗口；无法得到已用百分比时为 None。
    """

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
    """从 ``data`` 对象或响应顶层提取 GLM 额度项。

    ``data`` 是对象时只读取其中的 ``limits``，否则读取顶层 ``limits``；
    列表中不是对象的元素会被忽略。

    Args:
        raw: GLM 额度接口返回的 JSON 对象。
        mapping_type: 用于识别响应对象和额度项的运行时类型。

    Returns:
        额度项列表，以及与 ``limits`` 同层、供读取 ``level`` 的对象。
    """

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
    snapshot_without_windows: Callable[[str, int, Any], _SnapshotT],
    snapshot_type: Callable[..., _SnapshotT],
    provider: Any,
    ok_status: Any,
    no_data_status: Any,
    session_kind: Any,
    weekly_kind: Any,
    monthly_kind: Any,
    session_unit: int,
    weekly_unit: int,
    mapping_type: type[Any],
) -> _SnapshotT:
    """把 GLM 响应解析为统一额度快照。

    按五小时会话、每周和月度搜索的顺序添加可解析窗口，并从额度项所在对象读取
    非空字符串 ``level``；没有额度项或可用窗口时使用 ``no_data_status``。

    Args:
        raw: GLM 额度接口返回的 JSON 对象。
        account_id: 记录在快照中的本地账号 ID。
        fetched_at: 发起额度读取时的 Unix 毫秒时间戳。
        extract_limits: 从响应中提取额度项和外层对象的函数。
        find_limit: 按 GLM 的 ``type`` 和 ``unit`` 查找额度项的函数。
        build_window: 把原始额度项转换为统一窗口的函数。
        snapshot_without_windows: 没有额度项时创建不含窗口快照的函数。
        snapshot_type: 创建最终额度快照的构造函数。
        provider: 写入快照的模型服务商。
        ok_status: 至少成功解析一个窗口时写入的状态。
        no_data_status: 没有可用窗口时写入的状态。
        session_kind: 五小时会话窗口对应的统一类别。
        weekly_kind: 每周窗口对应的统一类别。
        monthly_kind: 月度搜索窗口对应的统一类别。
        session_unit: GLM 用于标识五小时会话窗口的 ``unit`` 值。
        weekly_unit: GLM 用于标识每周窗口的 ``unit`` 值。
        mapping_type: 判断额度项所在对象能否读取 ``level`` 的运行时类型。

    Returns:
        按传入类型和状态构造的统一额度快照。
    """

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
