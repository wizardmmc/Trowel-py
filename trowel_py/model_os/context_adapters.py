"""AgentEvent 到 Context Observer 标准事件的无 I/O 转换。"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import Any, TypeVar, cast

_CodexUsage = TypeVar("_CodexUsage")
_CodexCompaction = TypeVar("_CodexCompaction")
_CcEvent = TypeVar("_CcEvent")


def codex_events_from_agent(
    events: Sequence[Mapping[str, object]],
    *,
    mapping_type: Any,
    usage_type: Callable[..., _CodexUsage],
    compaction_type: Callable[..., _CodexCompaction],
    opt_str_fn: Callable[[object], str | None],
    opt_int_fn: Callable[[object], int | None],
    str_fn: Callable[[object], str],
    isinstance_fn: Callable[[object, Any], bool],
) -> list[_CodexUsage | _CodexCompaction]:
    """把 Codex 用量和压缩 AgentEvent 转换为标准事件，忽略其余事件。

    Args:
        events: 按发生顺序排列的统一 AgentEvent。
        mapping_type: 用于识别事件 payload 和嵌套字段是否为映射的类型。
        usage_type: Codex 标准用量事件的构造器。
        compaction_type: Codex 标准压缩事件的构造器。
        opt_str_fn: 把可空事件字段转换为字符串的函数。
        opt_int_fn: 把可空用量字段转换为整数的函数。
        str_fn: 把压缩阶段转换为字符串的函数。
        isinstance_fn: 按 ``mapping_type`` 判断字段是否为映射的函数。

    Returns:
        按输入顺序生成的 Codex 标准用量和压缩事件。
    """
    out: list[_CodexUsage | _CodexCompaction] = []
    for event in events:
        type_ = event.get("type")
        raw_payload = event.get("payload")
        payload = (
            cast(Mapping[str, object], raw_payload)
            if isinstance_fn(raw_payload, mapping_type)
            else {}
        )
        if type_ == "usage_updated":
            last = payload.get("last")
            total = payload.get("total")
            last_data = (
                cast(Mapping[str, object], last)
                if isinstance_fn(last, mapping_type)
                else {}
            )
            total_data = (
                cast(Mapping[str, object], total)
                if isinstance_fn(total, mapping_type)
                else {}
            )
            out.append(
                usage_type(
                    turn_id=opt_str_fn(event.get("turn_id")),
                    last_total_tokens=opt_int_fn(last_data.get("totalTokens")),
                    total_total_tokens=opt_int_fn(total_data.get("totalTokens")),
                    model_context_window=opt_int_fn(
                        payload.get("model_context_window")
                    ),
                )
            )
        elif type_ == "compaction":
            phase = payload.get("phase", "unknown")
            out.append(
                compaction_type(
                    phase=str_fn(phase),
                    turn_id=opt_str_fn(event.get("turn_id")),
                )
            )
    return out


def cc_events_from_agent(
    events: Sequence[Mapping[str, object]],
    *,
    mapping_type: Any,
    event_type: Callable[..., _CcEvent],
    opt_str_fn: Callable[[object], str | None],
    isinstance_fn: Callable[[object, Any], bool],
) -> list[_CcEvent]:
    """把 CC 用量和压缩边界 AgentEvent 转换为标准事件，忽略其余事件。

    Args:
        events: 按发生顺序排列的统一 AgentEvent。
        mapping_type: 用于识别事件 payload 和 usage 是否为映射的类型。
        event_type: CC 标准事件的构造器。
        opt_str_fn: 把可空事件字段转换为字符串的函数。
        isinstance_fn: 按 ``mapping_type`` 判断字段是否为映射的函数。

    Returns:
        按输入顺序生成的 CC 标准用量和压缩边界事件。
    """
    out: list[_CcEvent] = []
    for event in events:
        type_ = event.get("type")
        raw_payload = event.get("payload")
        payload = (
            cast(Mapping[str, object], raw_payload)
            if isinstance_fn(raw_payload, mapping_type)
            else {}
        )
        if type_ == "compact_boundary":
            out.append(
                event_type(
                    type="system",
                    subtype="compact_boundary",
                    timestamp=opt_str_fn(payload.get("timestamp")),
                    message_id=None,
                    model=None,
                    usage=None,
                    turn_id=opt_str_fn(event.get("turn_id")),
                    compact_trigger=opt_str_fn(payload.get("trigger")),
                )
            )
        elif type_ == "context_usage":
            usage = payload.get("usage")
            out.append(
                event_type(
                    type="assistant",
                    subtype=None,
                    timestamp=opt_str_fn(payload.get("timestamp")),
                    message_id=opt_str_fn(payload.get("message_id")),
                    model=opt_str_fn(payload.get("model")),
                    usage=usage if isinstance_fn(usage, mapping_type) else None,
                    turn_id=opt_str_fn(event.get("turn_id")),
                )
            )
    return out
