"""ContextSample journal payload 的无 I/O 编解码。"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any, TypeVar

_Sample = TypeVar("_Sample")


def sample_to_dict(sample: Any) -> dict[str, object]:
    return {
        "main_or_subagent": sample.main_or_subagent,
        "turn_id": sample.turn_id,
        "request_identity": sample.request_identity,
        "generation": sample.generation,
        "input_tokens": sample.input_tokens,
        "cache_creation_input_tokens": sample.cache_creation_input_tokens,
        "cache_read_input_tokens": sample.cache_read_input_tokens,
        "output_tokens": sample.output_tokens,
        "used_tokens": sample.used_tokens,
        "effective_window_tokens": sample.effective_window_tokens,
        "ratio": sample.ratio,
        "source": sample.source,
        "source_version": sample.source_version,
        "confidence": sample.confidence.value,
        "unavailable_reason": sample.unavailable_reason.value,
    }


def sample_from_dict(
    data: Mapping[str, object],
    native_session_id: str,
    *,
    sample_type: Callable[..., _Sample],
    opt_str_fn: Callable[[object], str | None],
    opt_int_fn: Callable[[object], int | None],
    opt_float_fn: Callable[[object], float | None],
    confidence_type: Callable[[str], Any],
    unavailable_reason_type: Callable[[str], Any],
    str_fn: Callable[[object], str],
    int_fn: Callable[[object], int],
) -> _Sample:
    return sample_type(
        native_session_id=native_session_id,
        main_or_subagent=data["main_or_subagent"],
        turn_id=opt_str_fn(data.get("turn_id")),
        request_identity=str_fn(data["request_identity"]),
        generation=int_fn(data["generation"]),
        input_tokens=opt_int_fn(data.get("input_tokens")),
        cache_creation_input_tokens=opt_int_fn(data.get("cache_creation_input_tokens")),
        cache_read_input_tokens=opt_int_fn(data.get("cache_read_input_tokens")),
        output_tokens=opt_int_fn(data.get("output_tokens")),
        used_tokens=opt_int_fn(data.get("used_tokens")),
        effective_window_tokens=opt_int_fn(data.get("effective_window_tokens")),
        ratio=opt_float_fn(data.get("ratio")),
        source=str_fn(data["source"]),
        source_version=opt_str_fn(data.get("source_version")),
        confidence=confidence_type(str_fn(data["confidence"])),
        unavailable_reason=unavailable_reason_type(str_fn(data["unavailable_reason"])),
    )
