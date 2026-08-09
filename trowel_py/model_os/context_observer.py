"""定义 Context Observer 数据模型，并转换运行事件、计算样本及编解码持久化 payload。

无法确认的占用必须表示为 ``unavailable``，不能伪装成零。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Callable, Iterable, Literal, Mapping, Sequence

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

# GLM 后端忽略 ``[1m]`` 后缀，实际窗口仍为 200k。
DEFAULT_CONTEXT_WINDOW = 200_000

# slash command 的本地输出会伪装成 assistant，但它不是模型调用。
SYNTHETIC_MODEL = "<synthetic>"

MainOrSubagent = Literal["main", "subagent"]


class ContextConfidence(str, Enum):
    """表示上下文观测用于容量判断的可信程度。"""

    RELIABLE = "reliable"
    WEAK = "weak"
    UNAVAILABLE = "unavailable"


class UnavailableReason(str, Enum):
    """说明无法计算占用比例的原因；可用观测使用 ``NONE``。"""

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
    """返回已知 CC 模型的有效上下文窗口。

    匹配时忽略大小写、首尾空白和 ``[1m]`` 后缀。

    Args:
        model: CC 事件报告的模型名；缺失、synthetic 或未知模型不提供可信窗口。

    Returns:
        匹配到的窗口 token 数；无法确认时返回 None。
    """

    if not model or model == SYNTHETIC_MODEL:
        return None
    lower = model.lower().replace("[1m]", "").strip()
    for prefix, window in _KNOWN_WINDOWS.items():
        if lower.startswith(prefix):
            return window
    return None


@dataclass(frozen=True)
class ContextSample:
    """记录一次主会话或子代理的上下文占用观测。

    ``ratio`` 为 None 表示当前占用比例不可用，不能按零占用处理。此时已知的
    token 数仍可保留。

    Attributes:
        native_session_id: Claude Code 会话 ID 或 Codex thread ID。
        main_or_subagent: 观测来自主会话还是子代理。
        turn_id: runtime 报告的 turn ID；未提供时为 None。
        request_identity: 用于构造幂等事件 ID；CC 使用 message ID，Codex 使用
            turn ID，缺失时分别为 ``"(no-id)"`` 和 ``"(no-turn)"``。
        generation: 当前原生会话已完成的上下文压缩次数，从 0 开始。
        input_tokens: CC 报告的输入 token 数；runtime 未拆分或数据不可用时为 None。
        cache_creation_input_tokens: CC 报告的 cache creation token 数；不可用时为 None。
        cache_read_input_tokens: CC 报告的 cache read token 数；不可用时为 None。
        output_tokens: CC 报告的输出 token 数；runtime 未拆分或数据不可用时为 None。
        used_tokens: 当前上下文占用 token 数；CC 为 input、cache creation、
            cache read 和 output token 之和，Codex 取 ``last_total_tokens``；
            无法确认时为 None。
        effective_window_tokens: 当前模型的有效上下文窗口；无法确认时为 None。
        ratio: ``used_tokens`` 占有效窗口的比例；无法计算时为 None。
        source: 观测来源，当前为 ``"cc"`` 或 ``"codex"``。
        source_version: 产生观测的 runtime 版本；未知时为 None。
        confidence: 观测是否可用于容量判断。
        unavailable_reason: 比例不可用的原因；可用观测为 ``NONE``。
    """

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
    """保存 CC 上下文计量所需的标准事件字段。

    Attributes:
        type: CC 事件类型，例如 ``assistant`` 或 ``system``。
        subtype: CC 事件子类型；非压缩边界等无子类型事件为 None。
        timestamp: CC 事件时间；未提供时为 None。
        message_id: assistant 消息 ID，用于合并流式更新；未提供时为 None。
        model: assistant 事件使用的模型名；未提供时为 None。
        usage: assistant usage 映射；None 按 usage 已脱敏处理，缺少
            ``input_tokens`` 时按 usage 缺失处理。
        turn_id: 事件所属的 turn ID；未提供时为 None。
        compact_trigger: 压缩触发方式；仅压缩边界可能提供。
        compact_pre_tokens: 压缩前 token 数；仅压缩边界可能提供。
        compact_post_tokens: 压缩后 token 数；仅压缩边界可能提供。
    """

    type: str
    subtype: str | None
    timestamp: str | None
    message_id: str | None
    model: str | None
    usage: Mapping[str, int] | None
    turn_id: str | None
    compact_trigger: str | None = None
    compact_pre_tokens: int | None = None
    compact_post_tokens: int | None = None


def _is_compact_boundary(ev: NormalizedCcEvent) -> bool:
    """判断事件是否为 CC 上下文压缩边界。"""
    return _run_is_compact_boundary(ev)


def _is_synthetic_assistant(ev: NormalizedCcEvent) -> bool:
    """判断事件是否为伪装成 assistant 的本地命令输出。"""
    return _run_is_synthetic_assistant(
        ev,
        synthetic_model=SYNTHETIC_MODEL,
    )


def _as_int(value: object) -> int:
    """把 CC usage 值转换为整数，布尔值和非法值按零处理。"""
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
    """把一条 CC assistant 事件转换为上下文占用样本。

    Args:
        ev: 待转换的 CC assistant 标准事件。
        generation: 当前 CC 会话已完成的上下文压缩次数。
        native_session_id: 产生事件的 Claude Code 会话 ID。
        main_or_subagent: 事件来自主会话还是子代理。
        source_version: 产生事件的 Claude Code 版本；未知时为 None。
        window: 当前模型的有效上下文窗口；未知时为 None。
        usage: 事件携带的原生 usage 字段；None 按已脱敏处理，缺少
            ``input_tokens`` 时按 usage 缺失处理。

    Returns:
        转换后的上下文占用样本。
    """
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
    window_resolver: Callable[[str | None], int | None] = resolve_window,
) -> list[ContextSample]:
    """从 CC 标准事件提取按消息去重的上下文占用样本。

    相同非空 ``message_id`` 的流式事件只保留最后一条；没有消息 ID 的事件全部
    保留。本地命令输出和非 assistant 事件不产生样本，压缩边界递增后续样本的代次。

    Args:
        events: 按发生顺序排列的 CC 标准事件。
        native_session_id: 产生事件的 Claude Code 会话 ID。
        main_or_subagent: 事件来自主会话还是子代理。
        source_version: 产生事件的 Claude Code 版本；未知时为 None。
        window_resolver: 按模型名返回有效上下文窗口的函数。

    Returns:
        按原始事件顺序排列的去重上下文占用样本。
    """

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
    """返回输入中最后一条主会话观测，不过滤不可用样本。

    Args:
        samples: 按时间先后顺序排列的主会话和子代理观测。

    Returns:
        最后一条主会话观测；不存在时返回 None。
    """

    return _run_latest_main_ratio(
        samples,
        list_fn=list,
        reversed_fn=reversed,
    )


@dataclass(frozen=True)
class NormalizedCodexUsage:
    """保存 Codex 上下文计量所需的一条 usage 观测。

    Attributes:
        turn_id: usage 所属的 Codex turn ID；缺失时样本不可用。
        last_total_tokens: Codex ``last.totalTokens`` 报告的当前上下文占用；
            缺失时样本不可用。
        total_total_tokens: Codex ``total.totalTokens`` 报告的会话累计 token 数；
            仅保留在标准事件中，不参与占用样本计算。
        model_context_window: Codex 报告的模型上下文窗口；缺失或非正数表示未知。
        timestamp: usage 事件时间；未提供时为 None。
    """

    turn_id: str | None
    last_total_tokens: int | None
    total_total_tokens: int | None
    model_context_window: int | None
    timestamp: str | None = None


@dataclass(frozen=True)
class NormalizedCodexCompaction:
    """保存 Codex 上下文压缩事件。

    Attributes:
        phase: 压缩阶段；只有 ``completed`` 推进上下文代次。
        turn_id: 触发压缩的 Codex turn ID；未提供时为 None。
        timestamp: 压缩事件时间；未提供时为 None。
    """

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
    """把一条 Codex usage 观测转换为上下文占用样本。

    Args:
        usage: 待转换的 Codex usage 观测。
        generation: 当前 Codex thread 已完成的上下文压缩次数。
        native_session_id: 产生观测的 Codex thread ID。
        source_version: 产生观测的 Codex 版本；未知时为 None。

    Returns:
        转换后的上下文占用样本。
    """
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
    """从 Codex 标准事件提取上下文占用样本并维护压缩代次。

    Args:
        events: 按发生顺序排列的 Codex usage 和压缩事件。
        native_session_id: 产生事件的 Codex thread ID。
        source_version: 产生事件的 Codex 版本；未知时为 None。

    Returns:
        按输入顺序排列的 usage 样本；只有 ``completed`` 压缩推进后续样本的代次。
    """

    return _run_extract_codex_samples(
        events,
        native_session_id=native_session_id,
        source_version=source_version,
        compaction_type=NormalizedCodexCompaction,
        isinstance_fn=isinstance,
        sample_from_usage_fn=_codex_sample,
    )


def context_sample_to_dict(sample: ContextSample) -> dict[str, object]:
    """把上下文占用样本转换为不含会话 ID 的持久化 payload。

    Args:
        sample: 待持久化的上下文占用样本。

    Returns:
        不含 ``native_session_id`` 的 JSON-safe 样本字段。
    """

    return _run_sample_to_dict(sample)


def context_sample_from_dict(
    d: Mapping[str, object], native_session_id: str
) -> ContextSample:
    """使用持久化 payload 和调用方提供的会话 ID 恢复上下文占用样本。

    Args:
        d: 保存的样本字段，不含原生会话 ID。
        native_session_id: payload 外保存的原生会话 ID；journal 回放时来自
            EventEnvelope。

    Returns:
        使用指定会话 ID 的上下文占用样本。
    """

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
    """把可选持久化字段转换为字符串，None 保持不变。"""
    return None if v is None else str(v)


def _opt_int(v: object) -> int | None:
    """把可选持久化值转换为整数。

    None 保持不变，布尔值归零，浮点数截断，可解析文本转为整数，非法值返回
    None。
    """
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
    """把可选持久化值转换为浮点数。

    None 保持不变，布尔值转为 0.0 或 1.0，可解析文本转为浮点数，非法值返回
    None。
    """
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
    """从 AgentEvent 中提取 Codex usage 和压缩标准事件。

    只接受 ``usage_updated`` 和 ``compaction``，其余事件忽略。

    Args:
        events: 按发生顺序排列的统一 AgentEvent。

    Returns:
        按输入顺序生成的 Codex 标准事件。
    """

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
    """从 AgentEvent 中提取 CC usage 和压缩边界标准事件。

    只接受 ``context_usage``、``compact_boundary``，以及 payload 含 usage
    映射的 ``finished``；其余事件忽略。

    Args:
        events: 按发生顺序排列的统一 AgentEvent。

    Returns:
        按输入顺序生成的 CC 标准事件。
    """

    return _run_cc_events_from_agent(
        events,
        mapping_type=Mapping,
        event_type=NormalizedCcEvent,
        opt_str_fn=_opt_str,
        isinstance_fn=isinstance,
    )
