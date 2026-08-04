"""把 telemetry 聚合仓储投影为不泄露原始属性的 Statistics read model。"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal, Protocol

from trowel_py.statistics.schemas import (
    CollectorStatistics,
    MetricAggregateData,
    SourceFreshness,
    SpanAggregateData,
    TelemetryStatisticsData,
)
from trowel_py.statistics.window import StatisticsWindow
from trowel_py.telemetry.catalog import DURATION_HISTOGRAM_UPPER_BOUNDS_MS
from trowel_py.telemetry.collector import CollectorSnapshot
from trowel_py.telemetry.storage import (
    MetricAggregate,
    SpanAggregate,
    epoch_ns_to_datetime,
)


class TelemetryReaderPort(Protocol):
    """声明 Statistics telemetry read model 使用的只读仓储操作。"""

    def watermarks(self) -> dict[str, datetime]:
        """返回 raw、hour 和 day 的独立处理水位。"""

        ...

    def database_sizes(self) -> dict[str, int]:
        """返回主库和 WAL/SHM 当前体积。"""

        ...

    def query_span_aggregates(
        self,
        start: datetime,
        end: datetime,
        *,
        resolution: Literal["hour", "day"],
    ) -> list[SpanAggregate]:
        """读取指定时间窗的 span 聚合。"""

        ...

    def query_metric_aggregates(
        self,
        start: datetime,
        end: datetime,
        *,
        resolution: Literal["hour", "day"],
    ) -> list[MetricAggregate]:
        """读取指定时间窗的 metric 聚合。"""

        ...


class CollectorSnapshotPort(Protocol):
    """声明统计 read model 读取 collector 状态的操作。"""

    def snapshot(self) -> CollectorSnapshot:
        """返回当前进程去身份化 collector 状态。"""

        ...


def build_telemetry_statistics(
    reader: TelemetryReaderPort,
    collector: CollectorSnapshotPort,
    window: StatisticsWindow,
    *,
    resolution: Literal["hour", "day"],
    generated_at: datetime | None = None,
) -> TelemetryStatisticsData:
    """读取聚合表并生成带时间窗、质量和新鲜度的统计响应。

    Args:
        reader: 只打开短连接的 telemetry 聚合仓储。
        collector: 当前进程 collector 状态来源。
        window: 已按调用方时区解析的半开时间窗。
        resolution: 返回小时或日聚合。
        generated_at: 测试可注入的响应生成时间。

    Returns:
        不含 attributes、session/call 引用或原始 span 的公开 read model。
    """

    span_rows = reader.query_span_aggregates(
        window.start,
        window.end,
        resolution=resolution,
    )
    metric_rows = reader.query_metric_aggregates(
        window.start,
        window.end,
        resolution=resolution,
    )
    watermarks = reader.watermarks()
    snapshot = collector.snapshot()
    sample_size = sum(row.sample_count for row in span_rows) + sum(
        row.sample_count for row in metric_rows
    )
    freshness = _telemetry_freshness(watermarks, resolution)
    return TelemetryStatisticsData(
        generated_at=generated_at or datetime.now(UTC),
        window_start=window.start,
        window_end=window.end,
        timezone=window.timezone,
        sample_size=sample_size,
        quality=_quality(sample_size, snapshot, freshness),
        freshness={"telemetry": freshness},
        resolution=resolution,
        collector=CollectorStatistics(
            accepted=snapshot.accepted,
            rejected=snapshot.rejected,
            dropped=snapshot.dropped,
            queued_records=snapshot.queued_records,
            inflight_records=snapshot.inflight_records,
            running=snapshot.running,
            last_error_category=snapshot.last_error_category,
        ),
        database_bytes=reader.database_sizes(),
        histogram_upper_bounds_ms=DURATION_HISTOGRAM_UPPER_BOUNDS_MS,
        spans=[_span_data(row) for row in span_rows],
        metrics=[_metric_data(row) for row in metric_rows],
    )


def _telemetry_freshness(
    watermarks: dict[str, datetime],
    resolution: str,
) -> SourceFreshness:
    """比较 raw 和目标聚合水位，生成来源新鲜度。"""

    aggregate = watermarks.get(resolution)
    if aggregate is None:
        return SourceFreshness(updated_at=None, status="unavailable")
    raw = watermarks.get("raw")
    status: Literal["fresh", "stale", "unavailable"] = "fresh"
    if raw is not None and aggregate < raw:
        status = "stale"
    return SourceFreshness(updated_at=aggregate, status=status)


def _quality(
    sample_size: int,
    snapshot: CollectorSnapshot,
    freshness: SourceFreshness,
) -> Literal["reliable", "partial", "unavailable"]:
    """根据样本、采集丢失和聚合新鲜度确定统一质量等级。"""

    if sample_size == 0 or freshness.status == "unavailable":
        return "unavailable"
    if snapshot.dropped or snapshot.rejected or freshness.status == "stale":
        return "partial"
    return "reliable"


def _span_data(row: SpanAggregate) -> SpanAggregateData:
    """把内部 span 聚合转换为不含空字符串占位的公开 DTO。"""

    return SpanAggregateData(
        bucket_start=epoch_ns_to_datetime(row.bucket_start_ns),
        component=row.component,
        operation=row.operation,
        status=row.status,
        runtime=row.runtime or None,
        model=row.model or None,
        sample_count=row.sample_count,
        duration_sum_ms=row.duration_sum_ms,
        duration_min_ms=row.duration_min_ms,
        duration_max_ms=row.duration_max_ms,
        histogram_counts=row.histogram_counts,
    )


def _metric_data(row: MetricAggregate) -> MetricAggregateData:
    """把内部 metric 聚合转换为不含空字符串占位的公开 DTO。"""

    return MetricAggregateData(
        bucket_start=epoch_ns_to_datetime(row.bucket_start_ns),
        component=row.component,
        name=row.name,
        kind=row.kind,
        unit=row.unit,
        operation=row.operation or None,
        status=row.status,
        runtime=row.runtime or None,
        model=row.model or None,
        sample_count=row.sample_count,
        value_sum=row.value_sum,
        value_min=row.value_min,
        value_max=row.value_max,
    )
