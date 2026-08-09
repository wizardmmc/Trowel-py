"""从 CC 标准事件计算上下文占用样本。"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from typing import Any, Protocol, TypeVar, cast


class CcContextEvent(Protocol):
    """描述上下文计算实际读取的 CC 事件字段。"""

    @property
    def type(self) -> str:
        """返回 CC 顶层事件类型。"""
        ...

    @property
    def subtype(self) -> str | None:
        """返回可选的 CC 事件子类型。"""
        ...

    @property
    def model(self) -> str | None:
        """返回事件报告的模型名。"""
        ...

    @property
    def turn_id(self) -> str | None:
        """返回事件所属的轮次 ID。"""
        ...

    @property
    def message_id(self) -> str | None:
        """返回用于合并流式更新的消息 ID。"""
        ...


_SampleT = TypeVar("_SampleT")


def is_compact_boundary(event: CcContextEvent) -> bool:
    """判断 CC 标准事件是否为上下文压缩边界。"""
    return event.type == "system" and event.subtype == "compact_boundary"


def is_synthetic_assistant(
    event: CcContextEvent, *, synthetic_model: object
) -> bool:
    """判断 assistant 事件是否来自本地命令而非模型调用。

    Args:
        event: 待判断的 CC 标准事件。
        synthetic_model: 本地命令输出使用的保留模型标记。
    """
    return event.type == "assistant" and event.model == synthetic_model


def as_int(
    value: object,
    *,
    bool_type: object,
    int_type: object,
    float_type: object,
    isinstance_fn: Callable[..., bool],
    int_fn: Callable[..., int],
    str_fn: Callable[..., str],
    exception_types: tuple[type[BaseException], ...],
) -> int:
    """把 usage 字段宽松转换为整数，布尔值和非法值按零处理。

    Args:
        value: 待转换的 usage 字段值。
        bool_type: 需要按零处理的布尔类型。
        int_type: 可以直接转换的整数类型。
        float_type: 可以截断为整数的浮点类型。
        isinstance_fn: 判断值是否属于指定类型的函数。
        int_fn: 执行整数转换的函数。
        str_fn: 在最终转换前生成文本的函数。
        exception_types: 转换失败时按零处理的异常类型。

    Returns:
        转换后的整数；布尔值或无法转换的值返回 0。
    """
    if isinstance_fn(value, bool_type):
        return 0
    if isinstance_fn(value, (int_type, float_type)):
        return int_fn(value)
    try:
        return int_fn(str_fn(value))
    except exception_types:
        return 0


def sample_from_event(
    event: CcContextEvent,
    *,
    generation: int,
    native_session_id: str,
    main_or_subagent: str,
    source_version: str | None,
    window: int | None,
    usage: Mapping[str, int] | None,
    mapping_type: object,
    sample_type: Callable[..., _SampleT],
    confidence_type: Any,
    unavailable_reason_type: Any,
    isinstance_fn: Callable[..., bool],
    as_int_fn: Callable[[object], int],
    round_fn: Callable[[float, int], float],
    dict_fn: Callable[..., dict[str, Any]],
) -> _SampleT:
    """把一条 CC assistant 事件转换为上下文占用样本。

    usage 缺失或上下文窗口未知时仍返回样本，并明确记录不可用原因。

    Args:
        event: 待转换的 CC assistant 标准事件。
        generation: 当前上下文压缩代次；0 表示尚未发生压缩，此后每经过一个压缩边界递增一次。
        native_session_id: 产生事件的 Claude Code 会话 ID。
        main_or_subagent: 事件来自主会话还是子代理。
        source_version: 产生事件的 Claude Code 版本；未知时为 None。
        window: 当前模型的有效上下文窗口 token 数；None 或非正数表示窗口未知。
        usage: 事件携带的原生 usage 字段；为 None 时按已脱敏处理，不含 input_tokens 时按缺失处理。
        mapping_type: 用于识别 usage 是否为映射的类型。
        sample_type: 上下文占用样本的构造器。
        confidence_type: 提供可靠与不可用状态的枚举类型。
        unavailable_reason_type: 提供窗口未知、usage 脱敏或缺失原因的枚举类型。
        isinstance_fn: 按 ``mapping_type`` 判断 usage 是否为映射的函数。
        as_int_fn: 把单个 usage 字段宽松转换为整数的函数。
        round_fn: 把占用比例保留四位小数的函数。
        dict_fn: 组装样本公共字段的映射构造器。

    Returns:
        包含 token 明细、占用比例及可信状态的上下文样本。
    """
    base = dict_fn(
        native_session_id=native_session_id,
        main_or_subagent=main_or_subagent,
        turn_id=event.turn_id,
        request_identity=event.message_id or "(no-id)",
        generation=generation,
        source="cc",
        source_version=source_version,
        effective_window_tokens=window,
    )
    has_usage = isinstance_fn(usage, mapping_type)
    usage_map = cast(Mapping[str, object], usage)
    has_usage = has_usage and "input_tokens" in usage_map
    if has_usage:
        input_tokens = as_int_fn(usage_map.get("input_tokens"))
        cache_creation_tokens = as_int_fn(usage_map.get("cache_creation_input_tokens"))
        cache_read_tokens = as_int_fn(usage_map.get("cache_read_input_tokens"))
        output_tokens = as_int_fn(usage_map.get("output_tokens"))
        used_tokens = (
            input_tokens + cache_creation_tokens + cache_read_tokens + output_tokens
        )
        if window is None or window <= 0:
            return sample_type(
                **base,
                input_tokens=input_tokens,
                cache_creation_input_tokens=cache_creation_tokens,
                cache_read_input_tokens=cache_read_tokens,
                output_tokens=output_tokens,
                used_tokens=used_tokens,
                ratio=None,
                confidence=confidence_type.UNAVAILABLE,
                unavailable_reason=unavailable_reason_type.UNKNOWN_WINDOW,
            )
        return sample_type(
            **base,
            input_tokens=input_tokens,
            cache_creation_input_tokens=cache_creation_tokens,
            cache_read_input_tokens=cache_read_tokens,
            output_tokens=output_tokens,
            used_tokens=used_tokens,
            ratio=round_fn(used_tokens / window, 4),
            confidence=confidence_type.RELIABLE,
            unavailable_reason=unavailable_reason_type.NONE,
        )

    # 无法读取有效 usage 时仍返回不可用样本，避免把未知占用误记为零或直接丢失观测。
    reason = (
        unavailable_reason_type.REDACTED_USAGE
        if usage is None
        else unavailable_reason_type.MISSING_USAGE
    )
    return sample_type(
        **base,
        input_tokens=None,
        cache_creation_input_tokens=None,
        cache_read_input_tokens=None,
        output_tokens=None,
        used_tokens=None,
        ratio=None,
        confidence=confidence_type.UNAVAILABLE,
        unavailable_reason=reason,
    )


def extract_samples(
    events: Sequence[Any],
    *,
    native_session_id: str,
    main_or_subagent: str,
    source_version: str | None,
    window_resolver: Callable[[str | None], int | None],
    is_compact_boundary_fn: Callable[..., bool],
    is_synthetic_assistant_fn: Callable[..., bool],
    sample_from_event_fn: Callable[..., Any],
    enumerate_fn: Callable[..., Iterable[tuple[int, Any]]],
    list_fn: Callable[[Iterable[Any]], list[Any]],
) -> list[Any]:
    """从 CC 事件提取上下文占用样本，按消息去重并标记压缩代次。

    函数忽略本地命令输出和非 assistant 事件；具有相同 ``message_id`` 的流式
    事件只保留最后一条，没有消息 ID 的事件则全部保留。

    Args:
        events: 按发生顺序排列的 CC 标准事件。
        native_session_id: 产生这些事件的 Claude Code 会话 ID。
        main_or_subagent: 这些事件来自主会话还是子代理。
        source_version: 产生事件的 Claude Code 版本；未知时为 None。
        window_resolver: 按模型名返回有效上下文窗口 token 数的函数。
        is_compact_boundary_fn: 判断事件是否为压缩边界的函数。
        is_synthetic_assistant_fn: 判断事件是否为本地命令输出的函数。
        sample_from_event_fn: 把 assistant 事件转换为上下文样本的函数。
        enumerate_fn: 为事件附加输入顺序的函数。
        list_fn: 把可迭代对象转换为列表的函数。

    Returns:
        按原始事件顺序排列的去重上下文占用样本。
    """
    latest_by_id: dict[str, tuple[int, int, Any]] = {}
    no_id: list[tuple[int, int, Any]] = []
    generation = 0
    for index, event in enumerate_fn(events):
        if is_compact_boundary_fn(event):
            generation += 1
            continue
        if is_synthetic_assistant_fn(event):
            continue
        if event.type != "assistant":
            continue
        record = (index, generation, event)
        if event.message_id:
            latest_by_id[event.message_id] = record
        else:
            no_id.append(record)

    merged = list_fn(latest_by_id.values()) + no_id
    merged.sort(key=lambda record: record[0])
    return [
        sample_from_event_fn(
            event,
            generation=event_generation,
            native_session_id=native_session_id,
            main_or_subagent=main_or_subagent,
            source_version=source_version,
            window=window_resolver(event.model),
            usage=event.usage,
        )
        for _, event_generation, event in merged
    ]


def latest_main_ratio(
    samples: Iterable[Any],
    *,
    list_fn: Callable[..., list[Any]],
    reversed_fn: Callable[..., Iterable[Any]],
) -> Any | None:
    """返回输入中最后一条主会话占用样本。

    Args:
        samples: 按时间排列的主会话和子代理占用样本。
        list_fn: 把样本转换为列表的函数。
        reversed_fn: 从末尾向前遍历样本的函数。

    Returns:
        最后一条主会话样本；没有主会话样本时返回 None。
    """
    for sample in reversed_fn(list_fn(samples)):
        if sample.main_or_subagent == "main":
            return sample
    return None
