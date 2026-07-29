"""从 Codex usage 与压缩事件计算上下文占用样本。"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any


def sample_from_usage(
    usage: Any,
    *,
    generation: int,
    native_session_id: str,
    source_version: str | None,
    sample_type: Callable[..., Any],
    confidence_type: Any,
    unavailable_reason_type: Any,
    dict_fn: Callable[..., dict[str, Any]],
    round_fn: Callable[[float, int], float],
) -> Any:
    """把一条 Codex usage 观测转换为上下文占用样本。

    当前占用只取 ``last_total_tokens``，不使用累计的 ``total_total_tokens``。
    缺少轮次 ID、当前占用或有效窗口时仍返回样本，并记录不可用原因。

    Args:
        usage: 待转换的 Codex usage 观测。
        generation: 当前 Codex thread 已完成的上下文压缩次数；尚未观察到 completed
            压缩事件时为 0。
        native_session_id: 产生观测的 Codex thread ID。
        source_version: 产生观测的 Codex 版本；未知时为 None。
        sample_type: 上下文占用样本的构造器。
        confidence_type: 提供观测可信度的枚举类型。
        unavailable_reason_type: 提供不可用原因的枚举类型。
        dict_fn: 组装样本公共字段的映射构造器。
        round_fn: 把占用比例保留四位小数的函数。

    Returns:
        包含当前占用、有效窗口、占用比例和可信状态的上下文样本。
    """
    base = dict_fn(
        native_session_id=native_session_id,
        main_or_subagent="main",
        turn_id=usage.turn_id,
        request_identity=usage.turn_id or "(no-turn)",
        generation=generation,
        source="codex",
        source_version=source_version,
    )
    if not usage.turn_id:
        return sample_type(
            **base,
            input_tokens=None,
            cache_creation_input_tokens=None,
            cache_read_input_tokens=None,
            output_tokens=None,
            used_tokens=None,
            effective_window_tokens=usage.model_context_window,
            ratio=None,
            confidence=confidence_type.UNAVAILABLE,
            unavailable_reason=unavailable_reason_type.MISSING_REQUEST_IDENTITY,
        )

    used_tokens = usage.last_total_tokens
    window = usage.model_context_window
    if used_tokens is None:
        return sample_type(
            **base,
            input_tokens=None,
            cache_creation_input_tokens=None,
            cache_read_input_tokens=None,
            output_tokens=None,
            used_tokens=None,
            effective_window_tokens=window,
            ratio=None,
            confidence=confidence_type.UNAVAILABLE,
            unavailable_reason=unavailable_reason_type.MISSING_USAGE,
        )
    if not window or window <= 0:
        return sample_type(
            **base,
            input_tokens=None,
            cache_creation_input_tokens=None,
            cache_read_input_tokens=None,
            output_tokens=None,
            used_tokens=used_tokens,
            effective_window_tokens=None,
            ratio=None,
            confidence=confidence_type.UNAVAILABLE,
            unavailable_reason=unavailable_reason_type.UNKNOWN_WINDOW,
        )
    return sample_type(
        **base,
        input_tokens=None,
        cache_creation_input_tokens=None,
        cache_read_input_tokens=None,
        output_tokens=None,
        used_tokens=used_tokens,
        effective_window_tokens=window,
        ratio=round_fn(used_tokens / window, 4),
        confidence=confidence_type.RELIABLE,
        unavailable_reason=unavailable_reason_type.NONE,
    )


def extract_samples(
    events: Sequence[Any],
    *,
    native_session_id: str,
    source_version: str | None,
    compaction_type: object,
    isinstance_fn: Callable[..., bool],
    sample_from_usage_fn: Callable[..., Any],
) -> list[Any]:
    """按事件顺序提取 Codex 上下文占用样本并维护压缩代次。

    压缩事件不产生样本；压缩事件的 ``phase`` 每出现一次 ``completed``，后续
    usage 的代次就加 1；``started`` 和未知 phase 不改变代次。

    Args:
        events: 按发生顺序排列的 Codex usage 和压缩事件。
        native_session_id: 产生这些事件的 Codex thread ID。
        source_version: 产生事件的 Codex 版本；未知时为 None。
        compaction_type: 用于识别压缩事件的类型。
        isinstance_fn: 判断事件是否属于压缩事件类型的函数。
        sample_from_usage_fn: 把 usage 事件转换为上下文占用样本的函数。

    Returns:
        按输入顺序排列的 usage 样本；压缩前的代次为 0。
    """
    samples: list[Any] = []
    generation = 0
    for event in events:
        if isinstance_fn(event, compaction_type):
            # started 和未知阶段尚不能证明压缩完成，因此不推进代次。
            if event.phase == "completed":
                generation += 1
            continue
        samples.append(
            sample_from_usage_fn(
                event,
                generation=generation,
                native_session_id=native_session_id,
                source_version=source_version,
            )
        )
    return samples
