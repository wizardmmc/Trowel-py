"""把双 runtime 的统一 session 事实聚合为 Agent 页 read model。"""

from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime
from typing import Protocol

from trowel_py.statistics.agent.models import (
    ActivityInterval,
    ModelObservation,
    Quality,
    SessionObservation,
    TokenUsage,
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
    observations.sort(key=lambda item: item.started_at, reverse=True)
    quality: Quality = (
        quality_worst(*(item.quality for item in observations))
        if observations
        else "unavailable"
    )
    all_intervals = [
        interval for item in observations for interval in item.intervals
    ]
    latest = max(
        (
            interval.end
            for item in observations
            for interval in item.intervals
        ),
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
            combine_token_usage([item.tokens for item in observations]),
            quality,
        ),
        first_visible_response=_distribution(
            [sample for item in observations for sample in item.response_samples],
            quality,
        ),
        activity=AgentActivityData(
            session_sum_ms=sum(
                _interval_duration_ms(list(item.intervals))
                for item in observations
            ),
            concurrent_union_ms=_interval_duration_ms(all_intervals),
            quality=(
                quality_worst(*(interval.quality for interval in all_intervals))
                if all_intervals
                else "unavailable"
            ),
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


def _token_data(tokens: TokenUsage, quality: Quality) -> AgentTokenUsageData:
    """把内部 token 值转换为明确包含缓存输入的公开 DTO。"""

    return AgentTokenUsageData(
        input=tokens.input,
        output=tokens.output,
        cache_read=tokens.cache_read,
        cache_creation=tokens.cache_creation,
        reasoning=tokens.reasoning,
        unknown=tokens.unknown,
        total=tokens.total,
        total_includes_cache_input=True,
        quality=quality if tokens.total is not None else "unavailable",
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

    grouped: dict[tuple[str, str | None], list[tuple[SessionObservation, ModelObservation]]] = {}
    for session in observations:
        for model in session.model_observations:
            grouped.setdefault((session.runtime, model.model), []).append((session, model))
    summaries = []
    for (runtime, model_name), rows in grouped.items():
        sessions_by_id = {session.session_id: session for session, _ in rows}
        sessions = list(sessions_by_id.values())
        models = [model for _, model in rows]
        group_quality = quality_worst(*(session.quality for session in sessions))
        tokens = combine_token_usage([model.tokens for model in models])
        ratio = (
            tokens.cache_read / tokens.input
            if tokens.cache_read is not None
            and tokens.input is not None
            and tokens.input > 0
            else None
        )
        summaries.append(
            AgentModelSummaryData(
                runtime=runtime,
                model=model_name,
                session_count=len(sessions),
                statuses=_status_counts(sessions),
                tokens=_token_data(tokens, group_quality),
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
    summaries.sort(key=lambda item: (-item.session_count, item.runtime, item.model or ""))
    return summaries


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
        tokens=_token_data(observation.tokens, observation.quality),
        status=observation.status,
        quality=observation.quality,
    )
