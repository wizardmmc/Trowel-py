"""验证总览组合保持来源质量、独立分母和缺失值。"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

from trowel_py.statistics.agent.schemas import (
    AgentActivityData,
    AgentLatencyDistributionData,
    AgentStatisticsData,
    AgentStatusCountsData,
    AgentTokenUsageData,
)
from trowel_py.statistics.memory.schemas import MemoryStatisticsData
from trowel_py.statistics.overview.service import compose_overview_statistics
from trowel_py.statistics.runtime.schemas import RuntimeStatisticsData
from trowel_py.statistics.schemas import SourceFreshness
from trowel_py.statistics.session_problems.schemas import SessionProblemListData
from trowel_py.statistics.window import parse_statistics_window

BASE = datetime(2026, 8, 3, 12, 0, tzinfo=UTC)


def test_overview_composes_public_models_without_recalculating_denominators() -> None:
    """总览复制各域事实，并把任一缺失来源降级为 partial。"""

    window = parse_statistics_window(date(2026, 8, 3), date(2026, 8, 3), "UTC")
    agent = _agent(window.start, window.end, token_total=150)
    memory = _memory(window.start, window.end)
    runtime = _runtime(window.start, window.end)
    problems = _problems(window.start, window.end)

    result = compose_overview_statistics(
        window,
        agent=agent,
        daily_agents=((date(2026, 8, 3), agent),),
        memory=memory,
        runtime=runtime,
        calls=None,
        session_problems=problems,
        generated_at=BASE,
    )

    assert result.quality == "partial"
    assert result.agent.user_sessions == 3
    assert result.agent.tokens.total == 150
    assert result.memory.search_hits == 47
    assert result.memory.reads == 5
    assert result.memory.helpful_rate.numerator == 6
    assert result.memory.helpful_rate.denominator == 13
    assert result.memory.recall_miss_rate.denominator == 147
    assert result.token_trend[0].token_total == 150
    assert result.database_files[0].name == "sessions.db"
    assert result.session_problems.items[0].trowel_session_id == "agent-a"
    assert {item.code for item in result.statuses} >= {
        "agent_interrupted",
        "memory_unread_hits",
        "rss_single_sample",
        "calls_unavailable",
    }
    assert result.sources["calls"].quality == "unavailable"


def test_overview_keeps_missing_daily_token_as_unavailable_instead_of_zero() -> None:
    """没有日账本时图表点保留 None，不能伪造零用量。"""

    window = parse_statistics_window(date(2026, 8, 3), date(2026, 8, 3), "UTC")

    result = compose_overview_statistics(
        window,
        agent=None,
        daily_agents=((date(2026, 8, 2), None),),
        memory=None,
        runtime=None,
        calls=None,
        session_problems=None,
        generated_at=BASE,
    )

    assert result.quality == "unavailable"
    assert result.token_trend[0].token_total is None
    assert result.token_trend[0].quality == "unavailable"
    assert result.agent.tokens.total is None
    assert all(item.total_bytes == 0 for item in result.database_files)
    assert all(item.quality == "unavailable" for item in result.database_files)
    assert {item.title for item in result.statuses} >= {
        "运行统计数据不可用",
        "会话问题数据不可用",
    }


def _agent(
    window_start: datetime,
    window_end: datetime,
    *,
    token_total: int,
) -> AgentStatisticsData:
    """构造已经由 Agent service 计算完成的公开 read model。"""

    quality = "reliable"
    statuses = AgentStatusCountsData(
        completed=1,
        running=1,
        interrupted=1,
        failed=0,
        unknown=0,
    )
    tokens = AgentTokenUsageData(
        input=100,
        output=20,
        cache_read=25,
        cache_creation=5,
        reasoning=0,
        unknown=0,
        total=token_total,
        known_session_count=3,
        session_count=3,
        quality=quality,
    )
    latency = AgentLatencyDistributionData(
        sample_size=5,
        p50_ms=1_000,
        p95_ms=None,
        p99_ms=None,
        quality=quality,
    )
    return AgentStatisticsData(
        generated_at=BASE,
        window_start=window_start,
        window_end=window_end,
        timezone="UTC",
        sample_size=3,
        quality=quality,
        freshness={"agent_sessions": SourceFreshness(updated_at=BASE, status="fresh")},
        statuses=statuses,
        tokens=tokens,
        first_visible_response=latency,
        activity=AgentActivityData(
            session_sum_ms=30_000,
            concurrent_union_ms=20_000,
            quality=quality,
        ),
        cache_input_ratio=0.25,
        model_summaries=[],
        sessions=[],
    )


def _memory(window_start: datetime, window_end: datetime) -> MemoryStatisticsData:
    """构造保留三套真实独立分母的 Memory 公开 read model。"""

    return MemoryStatisticsData.model_validate(
        {
            "generated_at": BASE,
            "window_start": window_start,
            "window_end": window_end,
            "timezone": "UTC",
            "sample_size": 64,
            "quality": "reliable",
            "freshness": {
                "access": {"updated_at": BASE, "status": "fresh"},
            },
            "sources": {},
            "attribution": {
                "attributed": 60,
                "unattributed": 4,
                "coverage": {
                    "numerator": 60,
                    "denominator": 64,
                    "ratio": 0.9375,
                    "quality": "reliable",
                },
                "quality": "reliable",
            },
            "retrieval": {
                "search_calls": 12,
                "nonempty_search_calls": 8,
                "empty_search_calls": 4,
                "search_hits": 47,
                "reads": 5,
                "read_sessions": 4,
                "read_rate": {
                    "numerator": 5,
                    "denominator": 47,
                    "ratio": 5 / 47,
                    "quality": "reliable",
                },
                "quality": "reliable",
            },
            "effect": {
                "helpful": 6,
                "harmful": 1,
                "unused": 6,
                "unknown": 1,
                "judged_user_sessions": 147,
                "eligible_user_sessions": 148,
                "judgement_coverage": {
                    "numerator": 147,
                    "denominator": 148,
                    "ratio": 147 / 148,
                    "quality": "reliable",
                },
                "helpful_rate": {
                    "numerator": 6,
                    "denominator": 13,
                    "ratio": 6 / 13,
                    "quality": "reliable",
                },
                "quality": "reliable",
            },
            "recall": {
                "retrieval_miss": 4,
                "awareness_miss": 2,
                "judged_user_sessions": 147,
                "miss_rate": {
                    "numerator": 6,
                    "denominator": 147,
                    "ratio": 6 / 147,
                    "quality": "reliable",
                },
                "quality": "reliable",
            },
            "assets": {
                "as_of": "2026-08-03",
                "active_notes": 120,
                "raw_reads": 90,
                "raw_harmful_outcomes": 1,
                "contradicted_or_superseded": 2,
                "harmful_high_notes": 0,
                "dictionary_status": "consistent",
                "dictionary_updated_at": BASE,
                "quality": "reliable",
            },
        }
    )


def _runtime(window_start: datetime, window_end: datetime) -> RuntimeStatisticsData:
    """构造含确定性缺口和受控文件名的运行公开 read model。"""

    gauge = {
        "value": 128 * 1024 * 1024,
        "unit": "By",
        "observed_at": BASE,
        "sample_size": 1,
        "quality": "partial",
    }
    return RuntimeStatisticsData.model_validate(
        {
            "generated_at": BASE,
            "window_start": window_start,
            "window_end": window_end,
            "timezone": "UTC",
            "sample_size": 4,
            "quality": "partial",
            "freshness": {
                "telemetry": {"updated_at": BASE, "status": "fresh"},
            },
            "resolution": "hour",
            "sidecar": {
                "uptime": {**gauge, "value": 3_600_000, "unit": "ms"},
                "rss": gauge,
                "restart_count": 0,
                "abnormal_exit_count": 0,
                "rss_series": [],
            },
            "last_clean_exit_at": BASE - timedelta(hours=1),
            "lifecycle": [],
            "fastapi": [],
            "sse": {
                "connect_count": 2,
                "disconnect_count": 1,
                "reconnect_count": 1,
                "operations": [],
                "quality": "partial",
            },
            "sqlite": {
                "busy_count": 0,
                "locked_count": 0,
                "operations": [],
                "files": [
                    {
                        "name": "sessions.db",
                        "owner": "memory.sessions",
                        "database_bytes": 4096,
                        "wal_bytes": 0,
                        "shm_bytes": 0,
                        "total_bytes": 4096,
                        "quality": "reliable",
                    }
                ],
                "quality": "partial",
            },
            "resources": [],
            "resource_remaining_count": 0,
            "gaps": [
                {
                    "code": "rss_single_sample",
                    "message": "RSS 只有一次采样，只展示事实，不判断上涨。",
                }
            ],
        }
    )


def _problems(window_start: datetime, window_end: datetime) -> SessionProblemListData:
    """构造只含可复制字段的会话问题公开列表。"""

    return SessionProblemListData.model_validate(
        {
            "generated_at": BASE,
            "window_start": window_start,
            "window_end": window_end,
            "timezone": "UTC",
            "sample_size": 1,
            "quality": "reliable",
            "freshness": {
                "session_problems": {"updated_at": BASE, "status": "fresh"},
            },
            "reviewed_session_count": 2,
            "problem_count": 1,
            "items": [
                {
                    "trowel_session_id": "agent-a",
                    "runtime": "codex",
                    "closed_at": BASE - timedelta(minutes=10),
                    "problem_text": "为什么这次调用出现了明显延迟？",
                }
            ],
            "next_cursor": None,
        }
    )
