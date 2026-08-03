"""验证 Agent session、分位数、活动区间和模型聚合。"""

from datetime import UTC, date, datetime, timedelta

from trowel_py.statistics.agent.models import (
    ActivityInterval,
    ModelObservation,
    SessionObservation,
    TokenUsage,
)
from trowel_py.statistics.agent.codec import clip_interval
from trowel_py.statistics.agent.service import build_agent_statistics
from trowel_py.statistics.window import parse_statistics_window

BASE = datetime(2026, 8, 3, 0, 0, tzinfo=UTC)


class FakeObservationReader:
    """返回测试预先构造的统一 session 事实。"""

    def __init__(self, observations: list[SessionObservation]) -> None:
        self._observations = observations

    def read(self, _window):
        """返回全部测试事实。"""

        return list(self._observations)


def test_agent_statistics_aggregates_sessions_models_and_activity() -> None:
    statuses = (
        ["completed"] * 16
        + ["running", "interrupted", "failed", "unknown"]
    )
    observations = [
        _observation(index, status)
        for index, status in enumerate(statuses)
    ]
    window = parse_statistics_window(date(2026, 8, 3), date(2026, 8, 3), "UTC")

    result = build_agent_statistics(
        FakeObservationReader(observations),
        window,
        generated_at=BASE + timedelta(hours=12),
    )

    assert result.sample_size == 20
    assert result.statuses.completed == 16
    assert result.statuses.running == 1
    assert result.statuses.interrupted == 1
    assert result.statuses.failed == 1
    assert result.statuses.unknown == 1
    assert result.first_visible_response.sample_size == 20
    assert result.first_visible_response.p50_ms == 9_500
    assert result.first_visible_response.p95_ms == 18_050
    assert result.first_visible_response.p99_ms is None
    assert result.activity.session_sum_ms == 200_000
    assert result.activity.concurrent_union_ms == 10_000
    assert len(result.model_summaries) == 2
    assert result.sessions[0].started_at > result.sessions[-1].started_at
    assert result.tokens.total == 1_050
    assert result.tokens.total_includes_cache_input is True


def test_percentile_thresholds_do_not_publish_undersized_samples() -> None:
    observations = [_observation(index, "completed") for index in range(4)]
    window = parse_statistics_window(date(2026, 8, 3), date(2026, 8, 3), "UTC")

    result = build_agent_statistics(FakeObservationReader(observations), window)

    distribution = result.first_visible_response
    assert distribution.sample_size == 4
    assert distribution.p50_ms is None
    assert distribution.p95_ms is None
    assert distribution.p99_ms is None
    assert distribution.quality == "unavailable"


def test_activity_interval_touching_window_start_does_not_overlap() -> None:
    """半开时间窗不接纳恰好在开始边界结束的活动。"""

    window = parse_statistics_window(date(2026, 8, 3), date(2026, 8, 3), "UTC")

    interval = clip_interval(
        BASE - timedelta(minutes=1),
        BASE,
        "reliable",
        window,
    )

    assert interval is None


def _observation(index: int, status: str) -> SessionObservation:
    """构造一个与其他 session 并发十秒的统一观察结果。"""

    runtime = "codex" if index % 2 else "claude_code"
    model = "gpt-5.6-sol" if runtime == "codex" else "glm-5.2"
    start = BASE + timedelta(minutes=index)
    return SessionObservation(
        session_id=f"session-{index:02d}",
        runtime=runtime,
        models=(model,),
        started_at=start,
        status=status,
        tokens=TokenUsage(
            input=index + 1,
            output=index + 1,
            cache_read=index + 1,
            cache_creation=index + 1 if runtime == "claude_code" else None,
            reasoning=index + 1 if runtime == "codex" else None,
            total=(index + 1) * 5,
        ),
        response_samples=(index * 1_000,),
        intervals=(ActivityInterval(BASE, BASE + timedelta(seconds=10), "reliable"),),
        quality="reliable",
        model_observations=(
            ModelObservation(
                model=model,
                tokens=TokenUsage(
                    input=index + 1,
                    output=index + 1,
                    cache_read=index + 1,
                    cache_creation=(
                        index + 1 if runtime == "claude_code" else None
                    ),
                    reasoning=index + 1 if runtime == "codex" else None,
                    total=(index + 1) * 5,
                ),
                response_samples=(index * 1_000,),
            ),
        ),
    )
