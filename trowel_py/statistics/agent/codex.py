"""把 Codex normalized turn journals 转换为统一 session 统计。"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime

from trowel_py.statistics.agent.codec import (
    clip_interval,
    is_in_window,
    iter_jsonl,
    mapping,
    parse_timestamp,
)
from trowel_py.statistics.agent.models import (
    CodexTurnSource,
    ModelObservation,
    SessionObservation,
    TokenUsage,
    combine_token_usage,
    combine_model_observations,
    quality_worst,
    subtract_token_usage,
)
from trowel_py.statistics.window import StatisticsWindow

_USAGE_FIELDS = {
    "inputTokens": "input",
    "outputTokens": "output",
    "cachedInputTokens": "cache_read",
    "reasoningOutputTokens": "reasoning",
    "totalTokens": "total",
}
_TERMINALS = frozenset({"finished", "interrupted", "error"})


def analyze_codex_session(
    sources: list[CodexTurnSource],
    window: StatisticsWindow,
) -> SessionObservation | None:
    """合并同一 Trowel session 的 Codex turns。

    Args:
        sources: 同一 Trowel session 的 normalized turn 来源。
        window: 查询时间窗。

    Returns:
        查询窗内统一 session 事实；所有 turn 都无交集时为 None。
    """

    turns = [
        observation
        for source in sources
        if (observation := _analyze_turn(source, window)) is not None
    ]
    if not turns:
        return None
    turns.sort(key=lambda item: item.started_at)
    models = sorted({model for turn in turns for model in turn.models})
    return SessionObservation(
        session_id=turns[0].session_id,
        runtime="codex",
        models=tuple(models),
        started_at=turns[0].started_at,
        status=turns[-1].status,
        tokens=combine_token_usage([turn.tokens for turn in turns]),
        response_samples=tuple(
            sample for turn in turns for sample in turn.response_samples
        ),
        intervals=tuple(interval for turn in turns for interval in turn.intervals),
        quality=quality_worst(*(turn.quality for turn in turns)),
        model_observations=combine_model_observations(
            [
                observation
                for turn in turns
                for observation in turn.model_observations
            ]
        ),
    )


def _analyze_turn(
    source: CodexTurnSource,
    window: StatisticsWindow,
) -> SessionObservation | None:
    """读取一个 normalized journal，并按累计水位生成窗口内增量。"""

    if not source.journal_path.is_file():
        registered_at = parse_timestamp(source.registered_at, local_naive=True)
        if not is_in_window(registered_at, window):
            return None
        return SessionObservation(
            session_id=source.session_id,
            runtime="codex",
            models=(source.model,) if source.model else (),
            started_at=registered_at,
            status=source.status,
            tokens=TokenUsage(),
            response_samples=(),
            intervals=(),
            quality="unavailable",
        )

    started_at: datetime | None = None
    first_visible_at: datetime | None = None
    terminal_at: datetime | None = None
    last_observed: datetime | None = None
    previous_total: TokenUsage | None = None
    usage_samples: list[TokenUsage] = []
    malformed_time = False
    with source.journal_path.open("rb") as handle:
        for event in iter_jsonl(handle):
            timestamp = parse_timestamp(event.get("timestamp"))
            if event.get("timestamp") is not None and timestamp is None:
                malformed_time = True
            if timestamp is not None:
                last_observed = timestamp
            event_type = event.get("type")
            payload = mapping(event.get("payload"))
            if event_type == "turn_started" and started_at is None:
                started_at = timestamp
            elif event_type == "assistant_delta" and first_visible_at is None:
                delta = payload.get("delta") if payload is not None else None
                if isinstance(delta, str) and delta:
                    first_visible_at = timestamp
            elif event_type in _TERMINALS:
                terminal_at = timestamp or terminal_at
            if event_type != "usage_updated" or payload is None:
                continue
            total = _codex_usage(payload.get("total"))
            last = _codex_usage(payload.get("last"))
            if previous_total is None:
                previous_total = subtract_token_usage(total, last)
            delta = subtract_token_usage(total, previous_total)
            previous_total = total
            if is_in_window(timestamp, window):
                usage_samples.append(delta)

    registered_at = parse_timestamp(source.registered_at, local_naive=True)
    started_at = started_at or registered_at
    terminal_at = terminal_at or parse_timestamp(
        source.completed_at,
        local_naive=True,
    )
    interval = clip_interval(
        started_at,
        terminal_at or last_observed,
        "reliable" if terminal_at is not None else "partial",
        window,
    )
    response_samples = ()
    if (
        is_in_window(started_at, window)
        and first_visible_at is not None
        and first_visible_at >= started_at
    ):
        response_samples = (
            round((first_visible_at - started_at).total_seconds() * 1000),
        )
    if not usage_samples and not response_samples and interval is None:
        return None
    if started_at is None:
        return None
    return SessionObservation(
        session_id=source.session_id,
        runtime="codex",
        models=(source.model,) if source.model else (),
        started_at=started_at,
        status=source.status,
        tokens=combine_token_usage(usage_samples),
        response_samples=response_samples,
        intervals=(interval,) if interval is not None else (),
        quality="partial" if malformed_time or terminal_at is None else "reliable",
        model_observations=(
            ModelObservation(
                model=source.model or None,
                tokens=combine_token_usage(usage_samples),
                response_samples=response_samples,
            ),
        ),
    )


def _codex_usage(raw: object) -> TokenUsage:
    """把 Codex total/last usage 转成共同累计水位。"""

    values: dict[str, int | None] = {
        "input": None,
        "output": None,
        "cache_read": None,
        "reasoning": None,
        "total": None,
    }
    if isinstance(raw, Mapping):
        for source_name, target_name in _USAGE_FIELDS.items():
            value = raw.get(source_name)
            if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
                values[target_name] = value
    return TokenUsage(**values)
