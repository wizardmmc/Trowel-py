"""并行读取各公开 Statistics read model，再交给纯总览服务组合。"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import TypeVar

from anyio import to_thread

from trowel_py.statistics.agent.schemas import AgentStatisticsData
from trowel_py.statistics.agent.service import (
    AgentObservationReaderPort,
    build_agent_statistics_many,
)
from trowel_py.statistics.calls.repository import CallStatisticsReader
from trowel_py.statistics.calls.service import build_call_list
from trowel_py.statistics.memory.service import (
    MemoryStatisticsReader,
    build_memory_statistics,
)
from trowel_py.statistics.runtime.service import (
    CollectorSnapshotPort,
    RuntimeObservationReaderPort,
    build_runtime_statistics,
)
from trowel_py.statistics.session_problems.service import (
    SessionProblemStatisticsReader,
    build_session_problem_list,
)
from trowel_py.statistics.window import StatisticsWindow, parse_statistics_window

from .schemas import OverviewStatisticsData
from .service import compose_overview_statistics

logger = logging.getLogger(__name__)

_Result = TypeVar("_Result")


@dataclass(frozen=True)
class OverviewSources:
    """保存总览允许调用的五个公开 read model 来源。

    Attributes:
        agent: 双 runtime Agent 观察来源；未装配时为 None。
        memory: Memory 公开统计来源；未装配时为 None。
        runtime: 运行统计观察来源；未装配时为 None。
        collector: 运行统计读取的 collector 快照；未装配时为 None。
        calls: 调用列表 reader；未装配时为 None。
        session_problems: 会话问题列表 reader；未装配时为 None。
    """

    agent: AgentObservationReaderPort | None
    memory: MemoryStatisticsReader | None
    runtime: RuntimeObservationReaderPort | None
    collector: CollectorSnapshotPort | None
    calls: CallStatisticsReader | None
    session_problems: SessionProblemStatisticsReader | None


async def load_overview_statistics(
    sources: OverviewSources,
    window: StatisticsWindow,
    *,
    generated_at: datetime | None = None,
) -> OverviewStatisticsData:
    """并行读取下游 read model，并让单个来源失败降级而非拖垮整页。

    Args:
        sources: 应用已装配的五个公开只读来源。
        window: 用户当前选择的共享 Statistics 时间窗。
        generated_at: 测试可注入的共同生成时刻。

    Returns:
        保留每个来源 unavailable 状态的总览公开 DTO。
    """

    now = generated_at or datetime.now(UTC)
    agent_models_task = _run_optional(
        "agent",
        (
            lambda: (
                _load_agent_models(sources.agent, window, now)
                if sources.agent is not None
                else None
            )
        ),
    )
    memory_task = _run_optional(
        "memory",
        (
            lambda: (
                build_memory_statistics(sources.memory, window, now=now)
                if sources.memory is not None
                else None
            )
        ),
    )
    runtime_task = _run_optional(
        "runtime",
        (
            lambda: (
                build_runtime_statistics(
                    sources.runtime,
                    sources.collector,
                    window,
                    generated_at=now,
                )
                if sources.runtime is not None and sources.collector is not None
                else None
            )
        ),
    )
    calls_task = _run_optional(
        "calls",
        (
            lambda: (
                build_call_list(sources.calls, window, limit=1)
                if sources.calls is not None
                else None
            )
        ),
    )
    problems_task = _run_optional(
        "session_problems",
        (
            lambda: (
                build_session_problem_list(
                    sources.session_problems,
                    window,
                    limit=5,
                    now=now,
                )
                if sources.session_problems is not None
                else None
            )
        ),
    )
    agent_models, memory, runtime, calls, problems = await asyncio.gather(
        agent_models_task,
        memory_task,
        runtime_task,
        calls_task,
        problems_task,
    )
    agent, trend = (
        agent_models if agent_models is not None else (None, _empty_agent_trend(window))
    )
    return compose_overview_statistics(
        window,
        agent=agent,
        daily_agents=trend,
        memory=memory,
        runtime=runtime,
        calls=calls,
        session_problems=problems,
        generated_at=now,
    )


async def _run_optional(
    source_name: str,
    operation: Callable[[], _Result],
) -> _Result | None:
    """在线程内执行同步 read model，并把来源失败降级为 None。

    Args:
        source_name: 写入日志的稳定来源名，不含动态身份。
        operation: 在当前工作线程内自行创建并关闭连接的同步读取。

    Returns:
        成功生成的公开 read model；读取失败时为 None。
    """

    try:
        return await to_thread.run_sync(operation)
    except Exception:
        logger.exception("[statistics.overview] %s source unavailable", source_name)
        return None


def _load_agent_models(
    reader: AgentObservationReaderPort,
    window: StatisticsWindow,
    generated_at: datetime,
) -> tuple[
    AgentStatisticsData,
    tuple[tuple[date, AgentStatisticsData], ...],
]:
    """一次读取生成当前窗和范围内各当地日期的 Agent 公共口径。

    Args:
        reader: 与 Agent 页相同的双 runtime 观察来源。
        window: 当前共享时间窗，并用于确定趋势终点和时区。
        generated_at: 全部 read model 共用的生成时刻。

    Returns:
        当前时间窗 read model，以及从旧到新的逐日 read model。
    """

    trend_windows = _trend_windows(window)
    models = build_agent_statistics_many(
        reader,
        (window, *(daily_window for _, daily_window in trend_windows)),
        generated_at=generated_at,
    )
    return models[0], tuple(
        (local_date, model)
        for (local_date, _), model in zip(
            trend_windows,
            models[1:],
            strict=True,
        )
    )


def _empty_agent_trend(
    window: StatisticsWindow,
) -> tuple[tuple[date, None], ...]:
    """返回所选范围内有日期但没有 token 值的趋势点。"""

    return tuple((local_date, None) for local_date, _ in _trend_windows(window))


def _trend_windows(
    window: StatisticsWindow,
) -> tuple[tuple[date, StatisticsWindow], ...]:
    """生成覆盖共享范围且能正确处理夏令时的逐个自然日窗。"""

    first_date = window.start.date()
    last_date = (window.end - timedelta(microseconds=1)).date()
    day_count = (last_date - first_date).days + 1
    dates = tuple(first_date + timedelta(days=offset) for offset in range(day_count))
    return tuple(
        (
            local_date,
            parse_statistics_window(local_date, local_date, window.timezone),
        )
        for local_date in dates
    )
