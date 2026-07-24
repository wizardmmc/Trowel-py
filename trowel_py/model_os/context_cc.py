"""CC 上下文占用的纯计算。"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from typing import Any, cast


def is_compact_boundary(event: Any) -> bool:
    return event.type == "system" and event.subtype == "compact_boundary"


def is_synthetic_assistant(event: Any, *, synthetic_model: object) -> bool:
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
    if isinstance_fn(value, bool_type):
        return 0
    if isinstance_fn(value, (int_type, float_type)):
        return int_fn(value)
    try:
        return int_fn(str_fn(value))
    except exception_types:
        return 0


def sample_from_event(
    event: Any,
    *,
    generation: int,
    native_session_id: str,
    main_or_subagent: str,
    source_version: str | None,
    window: int | None,
    usage: Mapping[str, int] | None,
    mapping_type: object,
    sample_type: Callable[..., Any],
    confidence_type: Any,
    unavailable_reason_type: Any,
    isinstance_fn: Callable[..., bool],
    as_int_fn: Callable[[object], int],
    round_fn: Callable[[float, int], float],
    dict_fn: Callable[..., dict[str, Any]],
) -> Any:
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

    # 缺失 usage 仍是一条不可用观测，不能静默丢弃或折算为零。
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
            # 同一消息的流式 usage 以最后一条为准。
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
    for sample in reversed_fn(list_fn(samples)):
        if sample.main_or_subagent == "main":
            return sample
    return None
