"""把 Codex normalized turn journals 转换为统一 session 统计。"""

from __future__ import annotations

import json
import mmap
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import BinaryIO

from trowel_py.statistics.agent.codec import (
    clip_interval,
    is_in_window,
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
_STATISTICS_EVENT_TYPES = frozenset(
    {"turn_started", "assistant_delta", "usage_updated", *_TERMINALS}
)
_STATISTICS_EVENT_PATTERN = re.compile(
    rb'"type"\s*:\s*"(?:turn_started|assistant_delta|usage_updated|finished|interrupted|error)"'
)
_TIMESTAMP_PATTERN = re.compile(rb'"timestamp"\s*:\s*"([^"\\]*)"')
_MAX_TOP_LEVEL_TYPE_OFFSET = 4_096


@dataclass(frozen=True)
class _CodexTurnFacts:
    """保存一次 journal 解析得到的全部可分窗事实。"""

    source: CodexTurnSource
    registered_at: datetime | None
    started_at: datetime | None
    first_visible_at: datetime | None
    terminal_at: datetime | None
    last_observed: datetime | None
    usage_samples: tuple[tuple[datetime | None, TokenUsage], ...]
    malformed_time: bool
    source_missing: bool


@dataclass(frozen=True)
class _CodexStatisticsEvent:
    """保存统计所需事件及其已解析时间。"""

    raw: Mapping[str, object]
    timestamp: datetime | None


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

    return analyze_codex_session_many(sources, (window,))[0]


def analyze_codex_session_many(
    sources: list[CodexTurnSource],
    windows: Sequence[StatisticsWindow],
) -> tuple[SessionObservation | None, ...]:
    """只解析一次 Codex journals，再投影到多个时间窗。

    Args:
        sources: 同一 Trowel session 的 normalized turn 来源。
        windows: 要从同一批 journal 生成的时间窗。

    Returns:
        与输入时间窗顺序一致的 session 事实；没有交集的位置为 None。
    """

    facts = [_parse_turn(source) for source in sources]
    return tuple(
        _combine_codex_turns(
            [
                observation
                for fact in facts
                if (observation := _project_turn(fact, window)) is not None
            ]
        )
        for window in windows
    )


def _combine_codex_turns(
    turns: list[SessionObservation],
) -> SessionObservation | None:
    """把一个时间窗内同一 Trowel session 的多个 turn 合并。"""

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
            [observation for turn in turns for observation in turn.model_observations]
        ),
    )


def _parse_turn(
    source: CodexTurnSource,
) -> _CodexTurnFacts:
    """读取一次 normalized journal，并保留后续分窗需要的事实。"""

    registered_at = parse_timestamp(source.registered_at, local_naive=True)
    if not source.journal_path.is_file():
        return _CodexTurnFacts(
            source=source,
            registered_at=registered_at,
            started_at=registered_at,
            first_visible_at=None,
            terminal_at=parse_timestamp(source.completed_at, local_naive=True),
            last_observed=None,
            usage_samples=(),
            malformed_time=False,
            source_missing=True,
        )

    started_at: datetime | None = None
    first_visible_at: datetime | None = None
    terminal_at: datetime | None = None
    last_observed: datetime | None = None
    previous_total: TokenUsage | None = None
    usage_samples: list[tuple[datetime | None, TokenUsage]] = []
    malformed_time = False
    with source.journal_path.open("rb") as handle:
        events, last_observed, malformed_time = _read_statistics_events(handle)
        for selected in events:
            event = selected.raw
            timestamp = selected.timestamp
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
            usage_samples.append((timestamp, delta))

    return _CodexTurnFacts(
        source=source,
        registered_at=registered_at,
        started_at=started_at or registered_at,
        first_visible_at=first_visible_at,
        terminal_at=terminal_at
        or parse_timestamp(source.completed_at, local_naive=True),
        last_observed=last_observed,
        usage_samples=tuple(usage_samples),
        malformed_time=malformed_time,
        source_missing=False,
    )


def _read_statistics_events(
    handle: BinaryIO,
) -> tuple[list[_CodexStatisticsEvent], datetime | None, bool]:
    """在 journal 中定位并只 JSON 解码统计真正使用的事件。

    normalized journal 可能包含很大的工具结果和正文。时间戳、事件类型都是
    Trowel 自己写入的顶层 ASCII 字段，因此先以内存映射在 C 层定位相关行；
    无关 payload 不进入 Python 逐行读取和 JSON 解码。末条记录仍用于运行中
    turn 的最新活动时间。

    Args:
        handle: 已打开的 Codex normalized turn journal。

    Returns:
        相关事件、全 journal 最新时间，以及是否出现无效时间字段。
    """

    if handle.seek(0, 2) == 0:
        return [], None, False
    handle.seek(0)
    selected: list[_CodexStatisticsEvent] = []
    seen_lines: set[int] = set()
    first_turn_started = False
    first_visible_delta = False
    last_observed: datetime | None = None
    malformed_time = False
    with mmap.mmap(handle.fileno(), length=0, access=mmap.ACCESS_READ) as mapped:
        for match in _STATISTICS_EVENT_PATTERN.finditer(mapped):
            line_start = mapped.rfind(b"\n", 0, match.start()) + 1
            if match.start() - line_start > _MAX_TOP_LEVEL_TYPE_OFFSET:
                continue
            first_type_key = mapped.find(b'"type"', line_start, match.end())
            if first_type_key != match.start():
                continue
            if line_start in seen_lines:
                continue
            seen_lines.add(line_start)
            line_end = mapped.find(b"\n", match.end())
            if line_end < 0:
                line_end = len(mapped)
            try:
                decoded = json.loads(mapped[line_start:line_end])
            except (UnicodeDecodeError, json.JSONDecodeError):
                continue
            if not isinstance(decoded, Mapping):
                continue
            event_type = decoded.get("type")
            if event_type not in _STATISTICS_EVENT_TYPES:
                continue
            if event_type == "turn_started":
                if first_turn_started:
                    continue
                first_turn_started = True
            elif event_type == "assistant_delta":
                payload = mapping(decoded.get("payload"))
                delta = payload.get("delta") if payload is not None else None
                if first_visible_delta or not isinstance(delta, str) or not delta:
                    continue
                first_visible_delta = True
            timestamp = parse_timestamp(decoded.get("timestamp"))
            if decoded.get("timestamp") is not None and timestamp is None:
                malformed_time = True
            elif timestamp is not None:
                last_observed = (
                    max(last_observed, timestamp)
                    if last_observed is not None
                    else timestamp
                )
            selected.append(_CodexStatisticsEvent(raw=decoded, timestamp=timestamp))

        last_line = _last_nonempty_line(mapped)
        timestamp_match = None
        for candidate in _TIMESTAMP_PATTERN.finditer(last_line):
            timestamp_match = candidate
        has_timestamp_key = b'"timestamp"' in last_line
        try:
            last_timestamp = (
                parse_timestamp(timestamp_match.group(1).decode("ascii"))
                if timestamp_match is not None
                else None
            )
        except UnicodeDecodeError:
            last_timestamp = None
        if has_timestamp_key and last_timestamp is None:
            malformed_time = True
        elif last_timestamp is not None:
            last_observed = (
                max(last_observed, last_timestamp)
                if last_observed is not None
                else last_timestamp
            )
    return selected, last_observed, malformed_time


def _last_nonempty_line(mapped: mmap.mmap) -> bytes:
    """从内存映射中取末条非空 JSONL 记录。"""

    end = len(mapped)
    while end > 0 and mapped[end - 1] in b" \t\r\n":
        end -= 1
    if end == 0:
        return b""
    start = mapped.rfind(b"\n", 0, end) + 1
    return mapped[start:end]


def _project_turn(
    facts: _CodexTurnFacts,
    window: StatisticsWindow,
) -> SessionObservation | None:
    """把一次 journal 解析结果裁剪到一个统计时间窗。"""

    source = facts.source
    if facts.source_missing:
        if facts.registered_at is None or not is_in_window(
            facts.registered_at, window
        ):
            return None
        return SessionObservation(
            session_id=source.session_id,
            runtime="codex",
            models=(source.model,) if source.model else (),
            started_at=facts.registered_at,
            status=source.status,
            tokens=TokenUsage(),
            response_samples=(),
            intervals=(),
            quality="unavailable",
        )

    started_at = facts.started_at
    terminal_at = facts.terminal_at
    usage_samples = [
        usage
        for timestamp, usage in facts.usage_samples
        if is_in_window(timestamp, window)
    ]
    interval = clip_interval(
        started_at,
        terminal_at or facts.last_observed,
        "reliable" if terminal_at is not None else "partial",
        window,
    )
    response_samples: tuple[int, ...] = ()
    if (
        started_at is not None
        and is_in_window(started_at, window)
        and facts.first_visible_at is not None
        and facts.first_visible_at >= started_at
    ):
        response_samples = (
            round((facts.first_visible_at - started_at).total_seconds() * 1000),
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
        quality=(
            "partial" if facts.malformed_time or terminal_at is None else "reliable"
        ),
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
