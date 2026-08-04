"""把 Claude Code binding transcript 转换为统一 session 统计。"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from trowel_py.statistics.agent.codec import (
    clip_interval,
    is_in_window,
    iter_jsonl_range,
    mapping,
    parse_timestamp,
)
from trowel_py.statistics.agent.models import (
    ClaudeBindingSource,
    ModelObservation,
    Quality,
    SessionObservation,
    TokenUsage,
    combine_token_usage,
    combine_model_observations,
)
from trowel_py.statistics.window import StatisticsWindow

_USAGE_FIELDS = {
    "input_tokens": "input",
    "output_tokens": "output",
    "cache_read_input_tokens": "cache_read",
    "cache_creation_input_tokens": "cache_creation",
}


@dataclass
class _ClaudeTurn:
    """保存 transcript 中一个用户 turn 的时间与首响模型事实。"""

    start: datetime | None
    first: datetime | None
    last: datetime | None
    first_model: str | None


def analyze_claude_binding(
    source: ClaudeBindingSource,
    window: StatisticsWindow,
) -> SessionObservation | None:
    """读取一个 CC binding，并按 message ID 去重 usage。

    Args:
        source: 带 Trowel 身份和字节水位的 transcript 来源。
        window: 查询时间窗。

    Returns:
        查询窗内的统一 session 事实；来源与时间窗无交集时为 None。
    """

    return analyze_claude_binding_many(source, (window,))[0]


def analyze_claude_binding_many(
    source: ClaudeBindingSource,
    windows: Sequence[StatisticsWindow],
) -> tuple[SessionObservation | None, ...]:
    """只解析一次 CC transcript，再投影到多个时间窗。

    Args:
        source: 带 Trowel 身份和字节水位的 transcript 来源。
        windows: 要从同一份 transcript 生成的时间窗。

    Returns:
        与输入时间窗顺序一致的 session 事实；没有交集的位置为 None。
    """

    bound_at = parse_timestamp(source.bound_at, local_naive=True)
    if source.start_offset is None or not source.transcript_path.is_file():
        return tuple(
            _missing_claude_observation(source, bound_at, window) for window in windows
        )

    seen_message_ids: set[str] = set()
    usage_samples: list[tuple[datetime | None, str | None, TokenUsage]] = []
    model_samples: list[tuple[datetime | None, str]] = []
    turns: list[_ClaudeTurn] = []
    current: _ClaudeTurn | None = None
    last_observed: datetime | None = bound_at

    for event in iter_jsonl_range(
        source.transcript_path,
        source.start_offset,
        min(source.end_offset, source.transcript_path.stat().st_size),
    ):
        timestamp = parse_timestamp(event.get("timestamp"))
        if timestamp is not None:
            last_observed = timestamp
        if _is_synthetic_assistant(event):
            continue
        if _is_visible_user(event):
            if current is not None:
                turns.append(current)
            current = _ClaudeTurn(
                start=timestamp,
                first=None,
                last=timestamp,
                first_model=None,
            )
        elif current is not None and event.get("type") == "assistant":
            current.last = timestamp or current.last
        if current is not None and current.first is None and _has_visible_text(event):
            current.first = timestamp
            message = mapping(event.get("message"))
            model = message.get("model") if message is not None else None
            current.first_model = model if isinstance(model, str) and model else None

        message = mapping(event.get("message"))
        if event.get("type") != "assistant" or message is None:
            continue
        model = message.get("model")
        if isinstance(model, str) and model:
            model_samples.append((timestamp, model))
        usage = mapping(message.get("usage"))
        message_id = message.get("id")
        if usage is None or not isinstance(message_id, str) or not message_id:
            continue
        if message_id in seen_message_ids:
            continue
        seen_message_ids.add(message_id)
        usage_samples.append(
            (
                timestamp,
                model if isinstance(model, str) and model else None,
                _claude_usage(usage),
            )
        )
    if current is not None:
        turns.append(current)

    completed_at = parse_timestamp(source.completed_at, local_naive=True)
    return tuple(
        _project_claude_observation(
            source,
            window,
            bound_at=bound_at,
            last_observed=last_observed,
            completed_at=completed_at,
            turns=turns,
            model_samples=model_samples,
            usage_samples=usage_samples,
        )
        for window in windows
    )


def _missing_claude_observation(
    source: ClaudeBindingSource,
    bound_at: datetime | None,
    window: StatisticsWindow,
) -> SessionObservation | None:
    """把缺少 transcript 或起始水位的 binding 投影到一个时间窗。"""

    if bound_at is None or not is_in_window(bound_at, window):
        return None
    return SessionObservation(
        session_id=source.session_id,
        runtime="claude_code",
        models=(source.model,) if source.model else (),
        started_at=bound_at,
        status=source.status,
        tokens=TokenUsage(),
        response_samples=(),
        intervals=(),
        quality="unavailable" if not source.transcript_path.is_file() else "partial",
    )


def _project_claude_observation(
    source: ClaudeBindingSource,
    window: StatisticsWindow,
    *,
    bound_at: datetime | None,
    last_observed: datetime | None,
    completed_at: datetime | None,
    turns: Sequence[_ClaudeTurn],
    model_samples: Sequence[tuple[datetime | None, str]],
    usage_samples: Sequence[tuple[datetime | None, str | None, TokenUsage]],
) -> SessionObservation | None:
    """把一次解析得到的 CC 事实裁剪到一个统计时间窗。"""

    window_usage = [
        (model, usage)
        for timestamp, model, usage in usage_samples
        if is_in_window(timestamp, window)
    ]
    models = {
        model for timestamp, model in model_samples if is_in_window(timestamp, window)
    }
    response_samples: list[int] = []
    model_response_samples: dict[str | None, list[int]] = {}
    intervals = []
    for index, turn in enumerate(turns):
        start = turn.start
        first = turn.first
        end = turn.last
        if (
            start is not None
            and is_in_window(start, window)
            and first is not None
            and first >= start
        ):
            latency = round((first - start).total_seconds() * 1000)
            response_samples.append(latency)
            first_model = turn.first_model
            model_response_samples.setdefault(
                first_model,
                [],
            ).append(latency)
        if index == len(turns) - 1 and completed_at is not None:
            end = max(value for value in (end, completed_at) if value is not None)
        interval = clip_interval(
            start,
            end,
            "reliable" if index < len(turns) - 1 or completed_at else "partial",
            window,
        )
        if interval is not None:
            intervals.append(interval)

    has_window_fact = bool(window_usage or response_samples or intervals)
    if not has_window_fact and not is_in_window(bound_at, window):
        return None
    started_at = next(
        (turn.start for turn in turns if turn.start is not None),
        bound_at or last_observed,
    )
    if started_at is None:
        return None
    if source.model and not models and is_in_window(bound_at, window):
        models.add(source.model)
    quality: Quality = "reliable" if completed_at is not None else "partial"
    return SessionObservation(
        session_id=source.session_id,
        runtime="claude_code",
        models=tuple(sorted(models)),
        started_at=started_at,
        status=source.status,
        tokens=combine_token_usage([usage for _, usage in window_usage]),
        response_samples=tuple(response_samples),
        intervals=tuple(intervals),
        quality=quality,
        model_observations=combine_model_observations(
            [
                ModelObservation(model=model, tokens=usage, response_samples=())
                for model, usage in window_usage
            ]
            + [
                ModelObservation(
                    model=model,
                    tokens=TokenUsage(),
                    response_samples=tuple(samples),
                )
                for model, samples in model_response_samples.items()
            ]
        ),
    )


def _claude_usage(raw: object) -> TokenUsage:
    """把一条 CC assistant usage 转成共同 token 分类。"""

    usage = mapping(raw)
    values: dict[str, int | None] = {
        "input": None,
        "output": None,
        "cache_read": None,
        "cache_creation": None,
    }
    if usage is not None:
        for source_name, target_name in _USAGE_FIELDS.items():
            value = usage.get(source_name)
            if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
                values[target_name] = value
    known = list(values.values())
    total = (
        sum(value or 0 for value in known)
        if all(value is not None for value in known)
        else None
    )
    return TokenUsage(**values, total=total)


def _is_visible_user(event: object) -> bool:
    """过滤 tool result、meta 和内部注入，只保留真实用户输入。"""

    raw = mapping(event)
    if raw is None or raw.get("type") != "user" or raw.get("isMeta"):
        return False
    message = mapping(raw.get("message"))
    content = message.get("content") if message is not None else None
    if isinstance(content, str):
        return _is_visible_user_text(content)
    if not isinstance(content, list):
        return False
    return any(
        isinstance(block, dict)
        and block.get("type") == "text"
        and _is_visible_user_text(str(block.get("text", "")))
        for block in content
    )


def _is_visible_user_text(text: str) -> bool:
    """判断一段 CC user 文本是否代表用户实际提交。"""

    stripped = text.lstrip()
    if not stripped or "<local-command-stdout>" in text:
        return False
    return not stripped.startswith(
        ("<task-notification>", "<system-reminder>", "<cparam>")
    )


def _has_visible_text(event: object) -> bool:
    """判断 CC assistant 记录是否包含非空可见文字。"""

    raw = mapping(event)
    message = mapping(raw.get("message")) if raw is not None else None
    content = message.get("content") if message is not None else None
    return bool(
        raw is not None
        and raw.get("type") == "assistant"
        and isinstance(content, list)
        and any(
            isinstance(block, dict)
            and block.get("type") == "text"
            and isinstance(block.get("text"), str)
            and block.get("text")
            for block in content
        )
    )


def _is_synthetic_assistant(event: object) -> bool:
    """判断记录是否是 Claude Code 生成的内部 synthetic assistant。"""

    raw = mapping(event)
    message = mapping(raw.get("message")) if raw is not None else None
    return bool(
        raw is not None
        and raw.get("type") == "assistant"
        and message is not None
        and message.get("model") == "<synthetic>"
    )
