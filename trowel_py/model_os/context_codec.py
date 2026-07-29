"""在上下文占用样本与持久化 payload 之间转换，不执行 I/O。"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any, TypeVar

_Sample = TypeVar("_Sample")


def sample_to_dict(sample: Any) -> dict[str, object]:
    """把上下文占用样本转换为不含会话 ID 的 payload。

    会话 ID 由调用方在 payload 外保存；写入 journal 时保存在 EventEnvelope 中。

    Args:
        sample: 待持久化的上下文占用样本。

    Returns:
        不含 ``native_session_id`` 的样本字段；``confidence`` 和
        ``unavailable_reason`` 写入枚举字符串值。
    """
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
    """使用 payload 和调用方提供的会话 ID 恢复上下文占用样本。

    会话 ID 只使用 ``native_session_id`` 参数，不读取 ``data`` 中的同名字段。

    Args:
        data: 保存的样本字段，不含原生会话 ID。
        native_session_id: payload 外保存的原生会话 ID；journal 回放时来自
            EventEnvelope。
        sample_type: 上下文占用样本的构造器。
        opt_str_fn: 转换可选文本字段的函数。
        opt_int_fn: 转换可选整数字段的函数。
        opt_float_fn: 转换可选浮点数字段的函数。
        confidence_type: 从字符串恢复观测可信度枚举的构造器。
        unavailable_reason_type: 从字符串恢复不可用原因的构造器。
        str_fn: 转换必填文本字段和枚举值的函数。
        int_fn: 转换压缩代次的函数。

    Returns:
        使用指定会话 ID 的上下文占用样本。
    """
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
