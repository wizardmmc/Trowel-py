"""把 CC 与 Codex 运行时事件归一化为可信的上下文占用观测。

无法确认的占用必须表示为 ``unavailable``，不能伪装成零。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Iterable, Literal, Mapping, Sequence

from .context_adapters import cc_events_from_agent as _run_cc_events_from_agent
from .context_adapters import codex_events_from_agent as _run_codex_events_from_agent
from .context_cc import as_int as _run_as_int
from .context_cc import extract_samples as _run_extract_cc_samples
from .context_cc import is_compact_boundary as _run_is_compact_boundary
from .context_cc import is_synthetic_assistant as _run_is_synthetic_assistant
from .context_cc import latest_main_ratio as _run_latest_main_ratio
from .context_cc import sample_from_event as _run_cc_sample
from .context_codex import extract_samples as _run_extract_codex_samples
from .context_codex import sample_from_usage as _run_codex_sample
from .context_codec import sample_from_dict as _run_sample_from_dict
from .context_codec import sample_to_dict as _run_sample_to_dict

# GLM 后端忽略 ``[1m]`` 后缀，有效窗口仍为 200k。
DEFAULT_CONTEXT_WINDOW = 200_000

# slash command 的本地输出会伪装成 assistant，但它不是模型调用。
SYNTHETIC_MODEL = "<synthetic>"

MainOrSubagent = Literal["main", "subagent"]


class ContextConfidence(str, Enum):
    """观测可信度；不可用不能伪装成零，缺窗口时仍可保留已知 token。"""

    RELIABLE = "reliable"
    WEAK = "weak"
    UNAVAILABLE = "unavailable"


class UnavailableReason(str, Enum):
    """不可用原因；枚举值供上层稳定区分展示。"""

    NONE = "none"
    UNKNOWN_MODEL = "unknown_model"
    UNKNOWN_WINDOW = "unknown_window"
    MISSING_USAGE = "missing_usage"
    REDACTED_USAGE = "redacted_usage"
    UNKNOWN_VERSION_SHAPE = "unknown_version_shape"
    MISSING_REQUEST_IDENTITY = "missing_request_identity"


_KNOWN_WINDOWS: Mapping[str, int] = {
    "glm-5": DEFAULT_CONTEXT_WINDOW,
    "glm-4": DEFAULT_CONTEXT_WINDOW,
    "claude-sonnet-4": DEFAULT_CONTEXT_WINDOW,
    "claude-opus-4": DEFAULT_CONTEXT_WINDOW,
    "claude-haiku-4": DEFAULT_CONTEXT_WINDOW,
}


def resolve_window(model: str | None) -> int | None:
    """解析可信窗口；未知或 synthetic 模型返回 ``None``。"""

    if not model or model == SYNTHETIC_MODEL:
        return None
    lower = model.lower().replace("[1m]", "").strip()
    for prefix, window in _KNOWN_WINDOWS.items():
        if lower.startswith(prefix):
            return window
    return None


@dataclass(frozen=True)
class ContextSample:
    """一次主会话或子代理的上下文占用观测。"""

    native_session_id: str
    main_or_subagent: MainOrSubagent
    turn_id: str | None
    request_identity: str
    generation: int
    input_tokens: int | None
    cache_creation_input_tokens: int | None
    cache_read_input_tokens: int | None
    output_tokens: int | None
    used_tokens: int | None
    effective_window_tokens: int | None
    ratio: float | None
    source: str
    source_version: str | None
    confidence: ContextConfidence
    unavailable_reason: UnavailableReason


@dataclass(frozen=True)
class NormalizedCcEvent:
    """CC 计量算法所需的标准事件字段。"""

    type: str
    subtype: str | None
    timestamp: str | None
    message_id: str | None
    model: str | None
    usage: Mapping[str, int] | None
    turn_id: str | None
    # 仅 compact_boundary 携带压缩前后数据。
    compact_trigger: str | None = None
    compact_pre_tokens: int | None = None
    compact_post_tokens: int | None = None


def _is_compact_boundary(ev: NormalizedCcEvent) -> bool:
    return _run_is_compact_boundary(ev)


def _is_synthetic_assistant(ev: NormalizedCcEvent) -> bool:
    return _run_is_synthetic_assistant(
        ev,
        synthetic_model=SYNTHETIC_MODEL,
    )


def _as_int(value: object) -> int:
    return _run_as_int(
        value,
        bool_type=bool,
        int_type=int,
        float_type=float,
        isinstance_fn=isinstance,
        int_fn=int,
        str_fn=str,
        exception_types=(TypeError, ValueError),
    )


def _cc_sample(
    ev: NormalizedCcEvent,
    *,
    generation: int,
    native_session_id: str,
    main_or_subagent: MainOrSubagent,
    source_version: str | None,
    window: int | None,
    usage: Mapping[str, int] | None,
) -> ContextSample:
    return _run_cc_sample(
        ev,
        generation=generation,
        native_session_id=native_session_id,
        main_or_subagent=main_or_subagent,
        source_version=source_version,
        window=window,
        usage=usage,
        mapping_type=Mapping,
        sample_type=ContextSample,
        confidence_type=ContextConfidence,
        unavailable_reason_type=UnavailableReason,
        isinstance_fn=isinstance,
        as_int_fn=_as_int,
        round_fn=round,
        dict_fn=dict,
    )


def extract_cc_samples(
    events: Sequence[NormalizedCcEvent],
    *,
    native_session_id: str,
    main_or_subagent: MainOrSubagent,
    source_version: str | None = None,
    window_resolver=resolve_window,
) -> list[ContextSample]:
    """提取去重后的 CC 上下文观测。"""

    return _run_extract_cc_samples(
        events,
        native_session_id=native_session_id,
        main_or_subagent=main_or_subagent,
        source_version=source_version,
        window_resolver=window_resolver,
        is_compact_boundary_fn=_is_compact_boundary,
        is_synthetic_assistant_fn=_is_synthetic_assistant,
        sample_from_event_fn=_cc_sample,
        enumerate_fn=enumerate,
        list_fn=list,
    )


def latest_main_ratio(samples: Iterable[ContextSample]) -> ContextSample | None:
    """返回最后一条主会话观测，忽略子代理样本。"""

    return _run_latest_main_ratio(
        samples,
        list_fn=list,
        reversed_fn=reversed,
    )


@dataclass(frozen=True)
class NormalizedCodexUsage:
    """Codex usage 观测；当前占用只取 ``last_total_tokens``。"""

    turn_id: str | None
    last_total_tokens: int | None
    total_total_tokens: int | None
    model_context_window: int | None
    timestamp: str | None = None


@dataclass(frozen=True)
class NormalizedCodexCompaction:
    """Codex 压缩事件；只有 completed 阶段形成新一代。"""

    phase: str
    turn_id: str | None
    timestamp: str | None = None


def _codex_sample(
    usage: NormalizedCodexUsage,
    *,
    generation: int,
    native_session_id: str,
    source_version: str | None,
) -> ContextSample:
    return _run_codex_sample(
        usage,
        generation=generation,
        native_session_id=native_session_id,
        source_version=source_version,
        sample_type=ContextSample,
        confidence_type=ContextConfidence,
        unavailable_reason_type=UnavailableReason,
        dict_fn=dict,
        round_fn=round,
    )


def extract_codex_samples(
    events: Sequence[NormalizedCodexUsage | NormalizedCodexCompaction],
    *,
    native_session_id: str,
    source_version: str | None = None,
) -> list[ContextSample]:
    """提取 Codex usage 观测并维护压缩代次。"""

    return _run_extract_codex_samples(
        events,
        native_session_id=native_session_id,
        source_version=source_version,
        compaction_type=NormalizedCodexCompaction,
        isinstance_fn=isinstance,
        sample_from_usage_fn=_codex_sample,
    )


def context_sample_to_dict(sample: ContextSample) -> dict:
    """生成 journal 使用的 JSON-safe payload。"""

    return _run_sample_to_dict(sample)


def context_sample_from_dict(
    d: Mapping[str, object], native_session_id: str
) -> ContextSample:
    """从 envelope 注入 session id 并恢复 ContextSample。"""

    return _run_sample_from_dict(
        d,
        native_session_id,
        sample_type=ContextSample,
        opt_str_fn=_opt_str,
        opt_int_fn=_opt_int,
        opt_float_fn=_opt_float,
        confidence_type=ContextConfidence,
        unavailable_reason_type=UnavailableReason,
        str_fn=str,
        int_fn=int,
    )


def _opt_str(v: object) -> str | None:
    return None if v is None else str(v)


def _opt_int(v: object) -> int | None:
    if v is None:
        return None
    if isinstance(v, bool):
        return 0
    if isinstance(v, (int, float)):
        return int(v)
    try:
        return int(str(v))
    except (TypeError, ValueError):
        return None


def _opt_float(v: object) -> float | None:
    if v is None:
        return None
    if isinstance(v, bool):
        return float(v)
    if isinstance(v, (int, float)):
        return float(v)
    try:
        return float(str(v))
    except (TypeError, ValueError):
        return None


def codex_context_events_from_agent(
    events: Sequence[Mapping[str, object]],
) -> list[NormalizedCodexUsage | NormalizedCodexCompaction]:
    """提取 Codex usage 与 compaction 标准事件。"""

    return _run_codex_events_from_agent(
        events,
        mapping_type=Mapping,
        usage_type=NormalizedCodexUsage,
        compaction_type=NormalizedCodexCompaction,
        opt_str_fn=_opt_str,
        opt_int_fn=_opt_int,
        str_fn=str,
        isinstance_fn=isinstance,
    )


def cc_context_events_from_agent(
    events: Sequence[Mapping[str, object]],
) -> list[NormalizedCcEvent]:
    """提取 CC context_usage 与 compact_boundary 标准事件。"""

    return _run_cc_events_from_agent(
        events,
        mapping_type=Mapping,
        event_type=NormalizedCcEvent,
        opt_str_fn=_opt_str,
        isinstance_fn=isinstance,
    )
