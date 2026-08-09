"""把双 runtime 的统一 session 事实聚合为 Agent 页 read model。"""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Protocol

from trowel_py.statistics.agent.models import (
    ActivityInterval,
    ModelObservation,
    Quality,
    SessionObservation,
    TokenUsage,
    RuntimeName,
    combine_token_usage,
    quality_worst,
)
from trowel_py.statistics.agent.schemas import (
    AgentActivityData,
    AgentLatencyDistributionData,
    AgentModelSummaryData,
    AgentSessionData,
    AgentStatisticsData,
    AgentStatusCountsData,
    AgentTokenUsageData,
)
from trowel_py.statistics.schemas import SourceFreshness
from trowel_py.statistics.window import StatisticsWindow

_RECENT_SESSION_LIMIT = 100


class AgentObservationReaderPort(Protocol):
    """声明 Agent statistics service 使用的只读 session 查询。"""

    def read(self, window: StatisticsWindow) -> list[SessionObservation]:
        """返回与查询窗相交的用户 session 观察结果。"""

        ...


def build_agent_statistics(
    reader: AgentObservationReaderPort,
    window: StatisticsWindow,
    *,
    generated_at: datetime | None = None,
) -> AgentStatisticsData:
    """读取统一 session 事实并生成 Agent 页响应。

    Args:
        reader: 双 runtime 只读 adapter 的组合入口。
        window: 已按调用方时区解析的半开时间窗。
        generated_at: 测试可注入的响应生成时间。

    Returns:
        不含消息正文、工作目录或原生会话 ID 的 Agent read model。
    """

    observations = reader.read(window)
    return _compose_agent_statistics(observations, window, generated_at=generated_at)


def build_agent_statistics_many(
    reader: AgentObservationReaderPort,
    windows: Sequence[StatisticsWindow],
    *,
    generated_at: datetime | None = None,
) -> tuple[AgentStatisticsData, ...]:
    """从一个 reader 快照生成多个 Agent 公开 read model。

    支持批量读取的生产 reader 只解析一次原生日志；测试或第三方 reader 没有
    ``read_many`` 时保持逐窗 ``read`` 的兼容行为。

    Args:
        reader: 双 runtime 只读 adapter 的组合入口。
        windows: 要生成的统计半开时间窗。
        generated_at: 测试可注入的共同生成时间。

    Returns:
        与输入时间窗顺序一致的 Agent 公开 read model。
    """

    requested = tuple(windows)
    batch_read = getattr(reader, "read_many", None)
    observations_by_window = (
        batch_read(requested)
        if callable(batch_read)
        else tuple(reader.read(window) for window in requested)
    )
    if len(observations_by_window) != len(requested):
        raise ValueError("agent batch reader returned an unexpected window count")
    return tuple(
        _compose_agent_statistics(
            observations,
            window,
            generated_at=generated_at,
        )
        for observations, window in zip(
            observations_by_window,
            requested,
            strict=True,
        )
    )


def _compose_agent_statistics(
    observations: list[SessionObservation],
    window: StatisticsWindow,
    *,
    generated_at: datetime | None,
) -> AgentStatisticsData:
    """把已经读取的统一 session 事实组合成一个 Agent 公开 DTO。"""

    observations.sort(key=lambda item: item.started_at, reverse=True)
    quality = _aggregate_quality([item.quality for item in observations])
    all_intervals = [interval for item in observations for interval in item.intervals]
    aggregate_tokens = combine_token_usage([item.tokens for item in observations])
    latest = max(
        (interval.end for item in observations for interval in item.intervals),
        default=max(
            (item.started_at for item in observations),
            default=None,
        ),
    )
    return AgentStatisticsData(
        generated_at=generated_at or datetime.now(UTC),
        window_start=window.start,
        window_end=window.end,
        timezone=window.timezone,
        sample_size=len(observations),
        quality=quality,
        freshness={
            "agent_sessions": SourceFreshness(
                updated_at=latest,
                status="fresh" if latest is not None else "unavailable",
            )
        },
        statuses=_status_counts(observations),
        tokens=_token_data(
            aggregate_tokens,
            quality,
            known_session_count=sum(
                1 for item in observations if item.tokens.total is not None
            ),
            session_count=len(observations),
        ),
        first_visible_response=_distribution(
            [sample for item in observations for sample in item.response_samples],
            quality,
        ),
        activity=AgentActivityData(
            session_sum_ms=sum(
                _interval_duration_ms(list(item.intervals)) for item in observations
            ),
            concurrent_union_ms=_interval_duration_ms(all_intervals),
            quality=(
                quality_worst(*(interval.quality for interval in all_intervals))
                if all_intervals
                else "unavailable"
            ),
        ),
        cache_input_ratio=_cache_input_ratio(
            [(item.runtime, item.tokens) for item in observations]
        ),
        model_summaries=_model_summaries(observations),
        sessions=[_session_data(item) for item in observations[:_RECENT_SESSION_LIMIT]],
    )


def _status_counts(observations: list[SessionObservation]) -> AgentStatusCountsData:
    """统计每种 session 终态的数量。"""

    counts = Counter(item.status for item in observations)
    return AgentStatusCountsData(
        completed=counts["completed"],
        running=counts["running"],
        interrupted=counts["interrupted"],
        failed=counts["failed"],
        unknown=counts["unknown"],
    )


def _token_data(
    tokens: TokenUsage,
    quality: Quality,
    *,
    known_session_count: int,
    session_count: int,
) -> AgentTokenUsageData:
    """把内部 token 小计、覆盖数量和缓存口径转换为公开 DTO。"""

    token_quality: Quality
    if tokens.total is None:
        token_quality = "unavailable"
    elif quality == "reliable" and known_session_count == session_count:
        token_quality = "reliable"
    else:
        token_quality = "partial"

    return AgentTokenUsageData(
        input=tokens.input,
        output=tokens.output,
        cache_read=tokens.cache_read,
        cache_creation=tokens.cache_creation,
        reasoning=tokens.reasoning,
        unknown=tokens.unknown,
        total=tokens.total,
        total_includes_cache_input=True,
        known_session_count=known_session_count,
        session_count=session_count,
        quality=token_quality,
    )


def _distribution(
    samples: list[int],
    quality: Quality,
) -> AgentLatencyDistributionData:
    """按 M12 样本门槛生成 p50、p95 和 p99。"""

    count = len(samples)
    return AgentLatencyDistributionData(
        sample_size=count,
        p50_ms=_percentile(samples, 0.50) if count >= 5 else None,
        p95_ms=_percentile(samples, 0.95) if count >= 20 else None,
        p99_ms=_percentile(samples, 0.99) if count >= 100 else None,
        quality=quality if count >= 5 else "unavailable",
    )


def _percentile(values: list[int], position: float) -> int:
    """用线性插值计算整数毫秒样本的分位数。"""

    ordered = sorted(values)
    point = (len(ordered) - 1) * position
    lower = int(point)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = point - lower
    return round(ordered[lower] + (ordered[upper] - ordered[lower]) * fraction)


def _merge_intervals(intervals: list[ActivityInterval]) -> list[ActivityInterval]:
    """合并重叠活动区间，并保留更保守的质量。"""

    ordered = sorted(intervals, key=lambda item: (item.start, item.end))
    merged: list[ActivityInterval] = []
    for current in ordered:
        if current.end < current.start:
            continue
        if not merged or current.start > merged[-1].end:
            merged.append(current)
            continue
        previous = merged[-1]
        merged[-1] = ActivityInterval(
            start=previous.start,
            end=max(previous.end, current.end),
            quality=quality_worst(previous.quality, current.quality),
        )
    return merged


def _interval_duration_ms(intervals: list[ActivityInterval]) -> int:
    """返回合并活动区间后的总毫秒数。"""

    return round(
        sum(
            (interval.end - interval.start).total_seconds()
            for interval in _merge_intervals(intervals)
        )
        * 1000
    )


def _model_summaries(
    observations: list[SessionObservation],
) -> list[AgentModelSummaryData]:
    """只使用模型事实切片生成 runtime/model 汇总。"""

    grouped: dict[
        tuple[RuntimeName, str | None], list[tuple[SessionObservation, ModelObservation]]
    ] = {}
    for session in observations:
        for model in session.model_observations:
            grouped.setdefault((session.runtime, model.model), []).append(
                (session, model)
            )
    summaries = []
    for (runtime, model_name), rows in grouped.items():
        sessions_by_id = {session.session_id: session for session, _ in rows}
        sessions = list(sessions_by_id.values())
        models = [model for _, model in rows]
        group_quality = _aggregate_quality(
            [session.quality for session in sessions]
        )
        tokens = combine_token_usage([model.tokens for model in models])
        ratio = _cache_input_ratio([(runtime, tokens)])
        summaries.append(
            AgentModelSummaryData(
                runtime=runtime,
                model=model_name,
                session_count=len(sessions),
                statuses=_status_counts(sessions),
                tokens=_token_data(
                    tokens,
                    group_quality,
                    known_session_count=len(
                        {
                            session.session_id
                            for session, model in rows
                            if model.tokens.total is not None
                        }
                    ),
                    session_count=len(sessions),
                ),
                first_visible_response=_distribution(
                    [sample for item in models for sample in item.response_samples],
                    group_quality,
                ),
                cache_input_ratio=ratio,
                activity_ms=sum(
                    _interval_duration_ms(list(session.intervals))
                    for session in sessions
                ),
                quality=group_quality,
            )
        )
    summaries.sort(
        key=lambda item: (-item.session_count, item.runtime, item.model or "")
    )
    return summaries


def _cache_input_ratio(
    values: Sequence[tuple[str, TokenUsage]],
) -> float | None:
    """按 runtime 原生 usage 语义计算缓存读取在全部输入中的占比。

    Claude Code 的普通输入、缓存读取和缓存创建是三个可相加分类；Codex 的
    cached input 是 input 的子集。先还原各 runtime 的全部输入分母，再跨会话
    汇总，避免 Claude Code 出现超过 100% 的比例。

    Args:
        values: runtime 名称和同一范围内对应的 token 用量。

    Returns:
        有完整分母时的缓存读取占比；没有可计算样本时为 None。
    """

    cache_read = 0
    total_input = 0
    known = False
    for runtime, tokens in values:
        if tokens.cache_read is None:
            continue
        if runtime == "claude_code":
            if tokens.input is None or tokens.cache_creation is None:
                continue
            denominator = tokens.input + tokens.cache_read + tokens.cache_creation
        elif runtime == "codex":
            if tokens.input is None:
                continue
            denominator = tokens.input
        else:
            continue
        if denominator <= 0:
            continue
        cache_read += tokens.cache_read
        total_input += denominator
        known = True
    return cache_read / total_input if known and total_input > 0 else None


def _session_data(observation: SessionObservation) -> AgentSessionData:
    """把一个内部 session 观察结果转换为公开明细行。"""

    return AgentSessionData(
        session_id=observation.session_id,
        runtime=observation.runtime,
        models=observation.models,
        started_at=observation.started_at,
        activity_ms=_interval_duration_ms(list(observation.intervals)),
        first_visible_response=_distribution(
            list(observation.response_samples),
            observation.quality,
        ),
        tokens=_token_data(
            observation.tokens,
            observation.quality,
            known_session_count=int(observation.tokens.total is not None),
            session_count=1,
        ),
        status=observation.status,
        quality=observation.quality,
    )


def _aggregate_quality(values: list[Quality]) -> Quality:
    """合并独立 session 质量；混合可用与不可用事实统一标为 partial。"""

    if not values or all(value == "unavailable" for value in values):
        return "unavailable"
    if all(value == "reliable" for value in values):
        return "reliable"
    return "partial"
