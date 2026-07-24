"""Codex 上下文占用的纯计算。"""

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
    samples: list[Any] = []
    generation = 0
    for event in events:
        if isinstance_fn(event, compaction_type):
            # 只有完成事件形成新一代，开始或未知阶段仅作审计。
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
