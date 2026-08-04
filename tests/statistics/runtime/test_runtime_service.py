"""验证运行统计的分位数门槛、错误计数和数据缺口。"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

from trowel_py.statistics.runtime.repository import RuntimeObservation
from trowel_py.statistics.runtime.schemas import DatabaseFileStatistics
from trowel_py.statistics.runtime.service import build_runtime_statistics
from trowel_py.statistics.window import parse_statistics_window
from trowel_py.telemetry.collector import CollectorSnapshot
from trowel_py.telemetry.contracts import datetime_to_epoch_ns
from trowel_py.telemetry.storage import (
    LatestMetric,
    LatestSpan,
    MetricAggregate,
    SpanAggregate,
)

BASE = datetime(2026, 8, 3, 12, 0, tzinfo=UTC)


class FakeRuntimeReader:
    """返回测试预置的运行观测。"""

    def __init__(self, observation: RuntimeObservation) -> None:
        """保存要返回的单次观测。"""

        self.observation = observation

    def read(self, _start, _end, *, resolution):
        """返回预置事实并保留生产调用形状。"""

        assert resolution == "hour"
        return self.observation


class HealthyCollector:
    """返回没有拒绝或丢弃的 collector 快照。"""

    def snapshot(self) -> CollectorSnapshot:
        """构造健康 collector 状态。"""

        return CollectorSnapshot(24, 0, 0, 0, 0, True, True, None)


def test_runtime_statistics_applies_percentile_thresholds_and_keeps_gaps() -> None:
    """p50/p95 按 5/20 门槛展示，单次 RSS 不被误判成趋势。"""

    histogram = (0, 0, 0, 0, 0, 20, 0, 0, 0, 0, 0, 0, 0, 0, 0)
    observation = RuntimeObservation(
        spans=(
            _span("desktop.start.sidecar_ready", 20, histogram),
            _span("desktop.start.first_screen", 4, _histogram(4)),
            _span("desktop.exit", 1, _histogram(1)),
            _span("sse.connect", 20, histogram),
        ),
        metrics=(
            _metric("sidecar.rss_bytes", 1, 128 * 1024 * 1024),
            _metric("sidecar.uptime_ms", 2, 1_800_000),
            _metric("resource.remaining", 2, 3),
        ),
        latest_spans=(
            LatestSpan(
                operation="desktop.exit",
                status="ok",
                ended_at_ns=datetime_to_epoch_ns(BASE),
                duration_ms=1_250,
            ),
        ),
        latest_metrics=(
            LatestMetric(
                name="sidecar.rss_bytes",
                operation="sidecar.sample",
                status="ok",
                observed_at_ns=datetime_to_epoch_ns(BASE),
                value=128 * 1024 * 1024,
            ),
            LatestMetric(
                name="sidecar.uptime_ms",
                operation="sidecar.sample",
                status="ok",
                observed_at_ns=datetime_to_epoch_ns(BASE),
                value=1_800_000,
            ),
            LatestMetric(
                name="resource.remaining",
                operation="resource.session.close",
                status="ok",
                observed_at_ns=datetime_to_epoch_ns(BASE),
                value=1,
            ),
        ),
        watermarks={"raw": BASE, "hour": BASE},
        files=(
            DatabaseFileStatistics(
                name="sessions.db",
                owner="memory.sessions",
                database_bytes=4096,
                wal_bytes=0,
                shm_bytes=0,
                total_bytes=4096,
                quality="reliable",
            ),
        ),
    )
    window = parse_statistics_window(date(2026, 8, 3), date(2026, 8, 3), "UTC")

    result = build_runtime_statistics(
        FakeRuntimeReader(observation),
        HealthyCollector(),
        window,
        generated_at=BASE + timedelta(minutes=1),
    )

    ready = result.lifecycle[0]
    screen = result.lifecycle[1]
    assert ready.sample_size == 20
    assert ready.p50_ms == 100
    assert ready.p95_ms == 100
    assert ready.p99_ms is None
    assert screen.sample_size == 4
    assert screen.p50_ms is None
    assert result.sidecar.rss.quality == "partial"
    assert result.sse.quality == "partial"
    assert result.resource_remaining_count == 1
    assert result.last_clean_exit_at == BASE
    assert {gap.code for gap in result.gaps} >= {
        "rss_single_sample",
        "p95_sample_short",
    }


def _span(
    operation: str,
    sample_count: int,
    histogram: tuple[int, ...],
) -> SpanAggregate:
    """构造一个小时 span 聚合。"""

    return SpanAggregate(
        bucket_start_ns=datetime_to_epoch_ns(BASE.replace(minute=0)),
        component="electron",
        operation=operation,
        status="ok",
        runtime="",
        model="",
        sample_count=sample_count,
        duration_sum_ms=sample_count * 75,
        duration_min_ms=50,
        duration_max_ms=90,
        histogram_counts=histogram,
    )


def _metric(name: str, sample_count: int, value: float) -> MetricAggregate:
    """构造一个小时 metric 聚合。"""

    return MetricAggregate(
        bucket_start_ns=datetime_to_epoch_ns(BASE.replace(minute=0)),
        component="runtime",
        name=name,
        kind="gauge",
        unit="By" if name.endswith("rss_bytes") else "ms",
        operation="sidecar.sample",
        status="ok",
        runtime="",
        model="",
        sample_count=sample_count,
        value_sum=value * sample_count,
        value_min=value,
        value_max=value,
    )


def _histogram(sample_count: int) -> tuple[int, ...]:
    """把全部测试样本放入 100ms bucket。"""

    return (0, 0, 0, 0, 0, sample_count, 0, 0, 0, 0, 0, 0, 0, 0, 0)
