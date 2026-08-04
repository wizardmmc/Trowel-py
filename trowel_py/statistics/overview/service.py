"""只组合各 Statistics 领域的公开 DTO，不读取原始事实源。"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, date, datetime

from trowel_py.statistics.agent.schemas import (
    AgentActivityData,
    AgentLatencyDistributionData,
    AgentStatisticsData,
    AgentStatusCountsData,
    AgentTokenUsageData,
)
from trowel_py.statistics.calls.schemas import CallListData
from trowel_py.statistics.memory.schemas import MemoryRatioData, MemoryStatisticsData
from trowel_py.statistics.runtime.schemas import (
    DatabaseFileStatistics,
    RuntimeStatisticsData,
)
from trowel_py.statistics.schemas import SourceFreshness
from trowel_py.statistics.session_problems.schemas import SessionProblemListData
from trowel_py.statistics.window import StatisticsWindow

from .schemas import (
    OverviewAgentData,
    OverviewMemoryData,
    OverviewQuality,
    OverviewSessionProblemsData,
    OverviewSourceData,
    OverviewStatisticsData,
    OverviewStatusData,
    OverviewTokenTrendPointData,
)

_SOURCE_LABELS = {
    "agent": "Agent",
    "memory": "Memory",
    "runtime": "运行",
    "calls": "调用详情",
    "session_problems": "会话问题",
}
_SOURCE_UNAVAILABLE_TITLES = {
    "agent": "Agent 数据不可用",
    "memory": "Memory 数据不可用",
    "runtime": "运行统计数据不可用",
    "calls": "调用详情数据不可用",
    "session_problems": "会话问题数据不可用",
}


def compose_overview_statistics(
    window: StatisticsWindow,
    *,
    agent: AgentStatisticsData | None,
    daily_agents: Sequence[tuple[date, AgentStatisticsData | None]],
    memory: MemoryStatisticsData | None,
    runtime: RuntimeStatisticsData | None,
    calls: CallListData | None,
    session_problems: SessionProblemListData | None,
    generated_at: datetime | None = None,
) -> OverviewStatisticsData:
    """把 L03 至 L07 的公开 read model 投影为总览。

    Args:
        window: 用户当前选择的共享 Statistics 时间窗。
        agent: Agent 页公开 read model；读取失败或未装配时为 None。
        daily_agents: 当前选择范围内各当地日期及 Agent 公开 read model。
        memory: Memory 页公开 read model；读取失败或未装配时为 None。
        runtime: 运行页公开 read model；读取失败或未装配时为 None。
        calls: 调用列表公开 read model；读取失败或未装配时为 None。
        session_problems: 会话问题公开 read model；读取失败或未装配时为 None。
        generated_at: 测试可注入的总览响应生成时刻。

    Returns:
        不含原始会话正文、路径、SQL 或工具参数的总览 DTO。
    """

    sources = _sources(agent, memory, runtime, calls, session_problems)
    return OverviewStatisticsData(
        generated_at=generated_at or datetime.now(UTC),
        window_start=window.start,
        window_end=window.end,
        timezone=window.timezone,
        sample_size=sum(
            1 for source in sources.values() if source.quality != "unavailable"
        ),
        quality=_combined_quality(tuple(source.quality for source in sources.values())),
        freshness=_all_freshness(agent, memory, runtime, calls, session_problems),
        agent=_agent_summary(agent),
        token_trend=[
            OverviewTokenTrendPointData(
                date=local_date,
                session_count=daily.sample_size if daily is not None else 0,
                known_token_session_count=(
                    daily.tokens.known_session_count if daily is not None else 0
                ),
                token_total=daily.tokens.total if daily is not None else None,
                quality=daily.tokens.quality if daily is not None else "unavailable",
            )
            for local_date, daily in daily_agents
        ],
        memory=_memory_summary(memory),
        statuses=_statuses(sources, agent, memory, runtime),
        session_problems=_problem_summary(session_problems),
        database_files=_database_files(runtime),
        sources=sources,
    )


def _agent_summary(agent: AgentStatisticsData | None) -> OverviewAgentData:
    """复制 Agent 公开汇总；缺失来源使用带 unavailable 的空形状。"""

    if agent is None:
        return OverviewAgentData(
            user_sessions=0,
            statuses=AgentStatusCountsData(
                completed=0,
                running=0,
                interrupted=0,
                failed=0,
                unknown=0,
            ),
            tokens=_unavailable_tokens(),
            first_visible_response=AgentLatencyDistributionData(
                sample_size=0,
                p50_ms=None,
                p95_ms=None,
                p99_ms=None,
                quality="unavailable",
            ),
            activity=AgentActivityData(
                session_sum_ms=0,
                concurrent_union_ms=0,
                quality="unavailable",
            ),
            quality="unavailable",
        )
    return OverviewAgentData(
        user_sessions=agent.sample_size,
        statuses=agent.statuses,
        tokens=agent.tokens,
        first_visible_response=agent.first_visible_response,
        activity=agent.activity,
        quality=agent.quality,
    )


def _memory_summary(memory: MemoryStatisticsData | None) -> OverviewMemoryData:
    """复制 Memory 各自已有的分子分母，不在总览重新计算比例。"""

    if memory is None:
        unavailable = _unavailable_ratio()
        return OverviewMemoryData(
            search_hits=0,
            reads=0,
            judged_effects=0,
            helpful=0,
            helpful_rate=unavailable,
            judgement_coverage=unavailable,
            recall_miss_rate=unavailable,
            attribution_coverage=unavailable,
            active_notes=0,
            quality="unavailable",
        )
    return OverviewMemoryData(
        search_hits=memory.retrieval.search_hits,
        reads=memory.retrieval.reads,
        judged_effects=memory.effect.helpful_rate.denominator,
        helpful=memory.effect.helpful,
        helpful_rate=memory.effect.helpful_rate,
        judgement_coverage=memory.effect.judgement_coverage,
        recall_miss_rate=memory.recall.miss_rate,
        attribution_coverage=memory.attribution.coverage,
        active_notes=memory.assets.active_notes,
        quality=memory.quality,
    )


def _problem_summary(
    problems: SessionProblemListData | None,
) -> OverviewSessionProblemsData:
    """保留会话问题公开字段，并把来源新鲜度归并成一个摘要。"""

    if problems is None:
        return OverviewSessionProblemsData(
            reviewed_session_count=0,
            problem_count=0,
            quality="unavailable",
            freshness=_unavailable_freshness(),
            items=[],
        )
    return OverviewSessionProblemsData(
        reviewed_session_count=problems.reviewed_session_count,
        problem_count=problems.problem_count,
        quality=problems.quality,
        freshness=_merged_freshness(problems.freshness),
        items=problems.items,
    )


def _database_files(
    runtime: RuntimeStatisticsData | None,
) -> list[DatabaseFileStatistics]:
    """返回固定三行数据库体积，缺失行保持 unavailable。"""

    known = (
        {item.name: item for item in runtime.sqlite.files}
        if runtime is not None
        else {}
    )
    owners = {
        "sessions.db": "memory.sessions",
        "workspaces.db": "desktop",
        "telemetry.db": "telemetry",
    }
    return [
        known.get(name)
        or DatabaseFileStatistics(
            name=name,
            owner=owner,
            database_bytes=0,
            wal_bytes=0,
            shm_bytes=0,
            total_bytes=0,
            quality="unavailable",
        )
        for name, owner in owners.items()
    ]


def _sources(
    agent: AgentStatisticsData | None,
    memory: MemoryStatisticsData | None,
    runtime: RuntimeStatisticsData | None,
    calls: CallListData | None,
    problems: SessionProblemListData | None,
) -> dict[str, OverviewSourceData]:
    """把五个公开 read model 统一为来源质量摘要。"""

    models = {
        "agent": agent,
        "memory": memory,
        "runtime": runtime,
        "calls": calls,
        "session_problems": problems,
    }
    return {
        name: OverviewSourceData(
            label=_SOURCE_LABELS[name],
            sample_size=model.sample_size if model is not None else 0,
            quality=model.quality if model is not None else "unavailable",
            freshness=(
                _merged_freshness(model.freshness)
                if model is not None
                else _unavailable_freshness()
            ),
        )
        for name, model in models.items()
    }


def _statuses(
    sources: dict[str, OverviewSourceData],
    agent: AgentStatisticsData | None,
    memory: MemoryStatisticsData | None,
    runtime: RuntimeStatisticsData | None,
) -> list[OverviewStatusData]:
    """使用固定计数和缺口代码生成状态，不判断语义原因。"""

    rows: list[OverviewStatusData] = []
    for source_name, source in sources.items():
        if source.quality == "unavailable":
            rows.append(
                OverviewStatusData(
                    code=f"{source_name}_unavailable",
                    level="unavailable",
                    title=_SOURCE_UNAVAILABLE_TITLES[source_name],
                    detail="该区域保留在页面中，不用 0 或估算值代替。",
                    source=source_name,
                    quality="unavailable",
                    freshness=source.freshness,
                )
            )
    if agent is not None:
        agent_freshness = sources["agent"].freshness
        status_specs = (
            (
                "agent_failed",
                "error",
                agent.statuses.failed,
                "执行失败",
                "只报告账本终态，不推断失败原因。",
            ),
            (
                "agent_interrupted",
                "warning",
                agent.statuses.interrupted,
                "被中断",
                "只报告账本终态，不推断中断原因。",
            ),
            (
                "agent_unknown",
                "unavailable",
                agent.statuses.unknown,
                "终态未知",
                "旧记录或来源不足，不能归入完成或失败。",
            ),
            (
                "agent_running",
                "info",
                agent.statuses.running,
                "仍在运行",
                "查询窗结束时仍有活动或尚未关闭。",
            ),
        )
        for code, level, count, label, detail in status_specs:
            if count:
                rows.append(
                    OverviewStatusData(
                        code=code,
                        level=level,
                        title=f"{count} 个用户 session {label}",
                        detail=detail,
                        source="agent_sessions",
                        quality=agent.quality,
                        freshness=agent_freshness,
                    )
                )
    if memory is not None and memory.retrieval.search_hits > memory.retrieval.reads:
        rows.append(
            OverviewStatusData(
                code="memory_unread_hits",
                level="info",
                title=(
                    "Memory 命中后读取为 "
                    f"{memory.retrieval.reads} / {memory.retrieval.search_hits}"
                ),
                detail="命中与读取使用不同含义，不合并成一个命中率。",
                source="memory",
                quality=memory.retrieval.quality,
                freshness=sources["memory"].freshness,
            )
        )
    if runtime is not None:
        runtime_freshness = sources["runtime"].freshness
        if runtime.resource_remaining_count:
            rows.append(
                OverviewStatusData(
                    code="resource_remaining",
                    level="warning",
                    title=f"最近核验仍有 {runtime.resource_remaining_count} 个资源残留",
                    detail="只报告资源账本数量，不根据数量推断泄漏原因。",
                    source="telemetry",
                    quality=runtime.quality,
                    freshness=runtime_freshness,
                )
            )
        if runtime.sidecar.abnormal_exit_count:
            rows.append(
                OverviewStatusData(
                    code="sidecar_abnormal_exit",
                    level="error",
                    title=f"sidecar 记录到 {runtime.sidecar.abnormal_exit_count} 次异常退出",
                    detail="异常退出来自受控生命周期终态。",
                    source="telemetry",
                    quality=runtime.quality,
                    freshness=runtime_freshness,
                )
            )
        lock_count = runtime.sqlite.busy_count + runtime.sqlite.locked_count
        if lock_count:
            rows.append(
                OverviewStatusData(
                    code="sqlite_lock_errors",
                    level="warning",
                    title=f"SQLite 记录到 {lock_count} 次锁冲突",
                    detail="数量来自 SQLITE_BUSY 与 SQLITE_LOCKED 分类之和。",
                    source="telemetry",
                    quality=runtime.sqlite.quality,
                    freshness=runtime_freshness,
                )
            )
        rows.extend(
            OverviewStatusData(
                code=gap.code,
                level="unavailable",
                title=gap.message,
                detail="运行统计保留了该采集缺口。",
                source="telemetry",
                quality=runtime.quality,
                freshness=runtime_freshness,
            )
            for gap in runtime.gaps
        )
    return rows


def _all_freshness(
    agent: AgentStatisticsData | None,
    memory: MemoryStatisticsData | None,
    runtime: RuntimeStatisticsData | None,
    calls: CallListData | None,
    problems: SessionProblemListData | None,
) -> dict[str, SourceFreshness]:
    """用领域前缀合并下游 freshness，避免同名来源互相覆盖。"""

    models = {
        "agent": agent,
        "memory": memory,
        "runtime": runtime,
        "calls": calls,
        "session_problems": problems,
    }
    return {
        f"{domain}.{source}": freshness
        for domain, model in models.items()
        if model is not None
        for source, freshness in model.freshness.items()
    }


def _merged_freshness(values: dict[str, SourceFreshness]) -> SourceFreshness:
    """取最新更新时间，并保留 stale 和 unavailable 的保守状态。"""

    timestamps = [item.updated_at for item in values.values() if item.updated_at]
    statuses = {item.status for item in values.values()}
    if not values or statuses == {"unavailable"}:
        status = "unavailable"
    elif "stale" in statuses:
        status = "stale"
    else:
        status = "fresh"
    return SourceFreshness(
        updated_at=max(timestamps) if timestamps else None,
        status=status,
    )


def _combined_quality(values: tuple[OverviewQuality, ...]) -> OverviewQuality:
    """全部可靠才为 reliable，全部不可用才为 unavailable，其余为 partial。"""

    if values and all(value == "reliable" for value in values):
        return "reliable"
    if not values or all(value == "unavailable" for value in values):
        return "unavailable"
    return "partial"


def _unavailable_tokens() -> AgentTokenUsageData:
    """构造所有分类未知且明确 unavailable 的 token 形状。"""

    return AgentTokenUsageData(
        input=None,
        output=None,
        cache_read=None,
        cache_creation=None,
        reasoning=None,
        unknown=None,
        total=None,
        known_session_count=0,
        session_count=0,
        quality="unavailable",
    )


def _unavailable_ratio() -> MemoryRatioData:
    """构造没有分母且不能解释为 0% 的比例。"""

    return MemoryRatioData(
        numerator=0,
        denominator=0,
        ratio=None,
        quality="unavailable",
    )


def _unavailable_freshness() -> SourceFreshness:
    """构造从未产出可核查事实的来源新鲜度。"""

    return SourceFreshness(updated_at=None, status="unavailable")
