"""把受控运行事实投影为分位数、错误、样本量和数据缺口。"""

from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta
from typing import Literal, Protocol

from trowel_py.statistics.runtime.repository import RuntimeObservation
from trowel_py.statistics.runtime.schemas import (
    RuntimeConnectionStatistics,
    RuntimeDistribution,
    RuntimeGap,
    RuntimeGauge,
    RuntimeGaugePoint,
    RuntimeStatisticsData,
    SQLiteStatistics,
    SidecarStatistics,
    StatisticsQuality,
)
from trowel_py.statistics.schemas import SourceFreshness
from trowel_py.statistics.window import StatisticsWindow
from trowel_py.telemetry.catalog import DURATION_HISTOGRAM_UPPER_BOUNDS_MS
from trowel_py.telemetry.collector import CollectorSnapshot
from trowel_py.telemetry.storage import (
    LatestMetric,
    MetricAggregate,
    SpanAggregate,
    epoch_ns_to_datetime,
)

LIFECYCLE_OPERATIONS: tuple[tuple[str, str], ...] = (
    ("desktop.start.sidecar_ready", "启动 → Sidecar 就绪"),
    ("desktop.start.first_screen", "启动 → 首屏可用"),
    ("resource.session.close", "Session 关闭"),
    ("desktop.exit", "应用退出 → 进程树终态"),
    ("desktop.reconcile", "崩溃残留恢复"),
)
FASTAPI_OPERATIONS: tuple[tuple[str, str], ...] = (
    ("http.agent.messages", "Agent 消息路由"),
    ("http.statistics.query", "统计查询路由"),
)
SSE_OPERATIONS: tuple[tuple[str, str], ...] = (
    ("sse.connect", "SSE 建连"),
    ("sse.first_event", "SSE 首事件"),
    ("sse.disconnect", "SSE 断线"),
    ("sse.reconnect", "SSE 重连"),
    ("sse.close", "SSE 关闭"),
)
SQLITE_OPERATIONS: tuple[tuple[str, str], ...] = (
    ("sqlite.sessions.read", "Sessions 读取"),
    ("sqlite.sessions.write", "Sessions 写入"),
    ("sqlite.workspaces.read", "Workspaces 读取"),
    ("sqlite.workspaces.write", "Workspaces 写入"),
)
RESOURCE_OPERATIONS: tuple[tuple[str, str], ...] = (
    ("resource.app.close", "App owner"),
    ("resource.runtime_connection.close", "Runtime connection owner"),
    ("resource.session.close", "Session owner"),
    ("resource.turn.close", "Turn owner"),
)
_RUNTIME_OPERATIONS = frozenset(
    operation
    for group in (
        LIFECYCLE_OPERATIONS,
        FASTAPI_OPERATIONS,
        SSE_OPERATIONS,
        SQLITE_OPERATIONS,
        RESOURCE_OPERATIONS,
    )
    for operation, _label in group
)
_RUNTIME_METRICS = frozenset(
    {
        "desktop.exit_terminal",
        "desktop.remaining_resources",
        "sidecar.uptime_ms",
        "sidecar.rss_bytes",
        "sidecar.restart",
        "sidecar.abnormal_exit",
        "sse.disconnect",
        "sse.reconnect",
        "sqlite.busy",
        "sqlite.locked",
        "resource.remaining",
    }
)


class RuntimeObservationReaderPort(Protocol):
    """声明运行 read model 使用的一次性事实读取操作。"""

    def read(
        self,
        start: datetime,
        end: datetime,
        *,
        resolution: Literal["hour", "day"],
    ) -> RuntimeObservation:
        """返回查询时间窗内的运行观测事实。"""

        ...


class CollectorSnapshotPort(Protocol):
    """声明运行 read model 读取 collector 健康状态的操作。"""

    def snapshot(self) -> CollectorSnapshot:
        """返回不含身份和正文的 collector 状态。"""

        ...


def build_runtime_statistics(
    reader: RuntimeObservationReaderPort,
    collector: CollectorSnapshotPort,
    window: StatisticsWindow,
    *,
    generated_at: datetime | None = None,
) -> RuntimeStatisticsData:
    """生成桌面运行页 read model。

    Args:
        reader: 聚合遥测、最新样本和受控文件大小来源。
        collector: 当前进程采集丢弃与运行状态来源。
        window: 已按调用方时区解析的半开时间窗。
        generated_at: 测试可注入的响应生成时刻。

    Returns:
        不含 SQL、路径、动态路由或原始身份的运行统计。
    """

    resolution = _resolution(window)
    observation = reader.read(window.start, window.end, resolution=resolution)
    snapshot = collector.snapshot()
    span_rows = tuple(row for row in observation.spans if row.operation in _RUNTIME_OPERATIONS)
    metric_rows = tuple(row for row in observation.metrics if row.name in _RUNTIME_METRICS)
    freshness = _freshness(observation, resolution)
    lifecycle = _distributions(span_rows, LIFECYCLE_OPERATIONS)
    fastapi = _distributions(span_rows, FASTAPI_OPERATIONS)
    sse_operations = _distributions(span_rows, SSE_OPERATIONS)
    sqlite_operations = _distributions(span_rows, SQLITE_OPERATIONS)
    resources = _distributions(span_rows, RESOURCE_OPERATIONS)
    latest_metrics = {item.name: item for item in observation.latest_metrics}
    uptime = _gauge("sidecar.uptime_ms", "ms", latest_metrics, metric_rows)
    rss = _gauge("sidecar.rss_bytes", "By", latest_metrics, metric_rows)
    last_clean_exit_at = _last_clean_exit(observation)
    sample_size = sum(row.sample_count for row in span_rows) + sum(
        row.sample_count for row in metric_rows
    )
    gaps = _gaps(rss, last_clean_exit_at, lifecycle + fastapi + sse_operations + sqlite_operations, freshness)
    return RuntimeStatisticsData(
        generated_at=generated_at or datetime.now(UTC),
        window_start=window.start,
        window_end=window.end,
        timezone=window.timezone,
        sample_size=sample_size,
        quality=_overall_quality(sample_size, snapshot, freshness),
        freshness={"telemetry": freshness},
        resolution=resolution,
        sidecar=SidecarStatistics(
            uptime=uptime,
            rss=rss,
            restart_count=_metric_total(metric_rows, "sidecar.restart"),
            abnormal_exit_count=_metric_total(metric_rows, "sidecar.abnormal_exit"),
            rss_series=_gauge_series(metric_rows, "sidecar.rss_bytes"),
        ),
        last_clean_exit_at=last_clean_exit_at,
        lifecycle=lifecycle,
        fastapi=fastapi,
        sse=RuntimeConnectionStatistics(
            connect_count=_span_count(span_rows, "sse.connect"),
            disconnect_count=max(
                _span_count(span_rows, "sse.disconnect"),
                _metric_total(metric_rows, "sse.disconnect"),
            ),
            reconnect_count=max(
                _span_count(span_rows, "sse.reconnect"),
                _metric_total(metric_rows, "sse.reconnect"),
            ),
            operations=sse_operations,
            quality=_group_quality(sse_operations),
        ),
        sqlite=SQLiteStatistics(
            busy_count=_metric_total(metric_rows, "sqlite.busy"),
            locked_count=_metric_total(metric_rows, "sqlite.locked"),
            operations=sqlite_operations,
            files=list(observation.files),
            quality=_group_quality(sqlite_operations),
        ),
        resources=resources,
        resource_remaining_count=_latest_metric_int(
            latest_metrics,
            "resource.remaining",
        ),
        gaps=gaps,
    )


def _resolution(window: StatisticsWindow) -> Literal["hour", "day"]:
    """较短时间窗用小时桶，长时间窗用日桶。"""

    return "hour" if window.end - window.start <= timedelta(days=31) else "day"


def _distributions(
    rows: tuple[SpanAggregate, ...],
    operations: tuple[tuple[str, str], ...],
) -> list[RuntimeDistribution]:
    """按固定 operation 合并跨桶、跨状态直方图。

    Args:
        rows: 已过滤到运行领域的 span 聚合。
        operations: 固定 operation 和展示名称。

    Returns:
        保持目录顺序且包含 unavailable 行的分布列表。
    """

    result: list[RuntimeDistribution] = []
    for operation, label in operations:
        selected = [row for row in rows if row.operation == operation]
        sample_size = sum(row.sample_count for row in selected)
        error_count = sum(
            row.sample_count for row in selected if row.status == "error"
        )
        histogram = [0] * len(DURATION_HISTOGRAM_UPPER_BOUNDS_MS)
        maximum = 0.0
        for row in selected:
            maximum = max(maximum, row.duration_max_ms)
            for index, count in enumerate(row.histogram_counts):
                histogram[index] += count
        result.append(
            RuntimeDistribution(
                operation=operation,
                label=label,
                sample_size=sample_size,
                error_count=error_count,
                p50_ms=_percentile(histogram, maximum, 0.50, minimum_samples=5),
                p95_ms=_percentile(histogram, maximum, 0.95, minimum_samples=20),
                p99_ms=_percentile(histogram, maximum, 0.99, minimum_samples=100),
                quality=_distribution_quality(sample_size),
            )
        )
    return result


def _percentile(
    histogram: list[int],
    maximum: float,
    quantile: float,
    *,
    minimum_samples: int,
) -> float | None:
    """从可合并非累计直方图估算一个有门槛的分位数。

    Args:
        histogram: 与集中毫秒上界一一对应的非累计计数。
        maximum: 该操作跨桶的实际最大耗时。
        quantile: 0 到 1 之间的目标分位。
        minimum_samples: 产品允许展示该分位数的最小样本量。

    Returns:
        首个达到目标的 bucket 上界；最后无穷桶返回实际最大值；样本不足为 None。
    """

    sample_size = sum(histogram)
    if sample_size < minimum_samples:
        return None
    target = math.ceil(sample_size * quantile)
    seen = 0
    for upper_bound, count in zip(DURATION_HISTOGRAM_UPPER_BOUNDS_MS, histogram):
        seen += count
        if seen >= target:
            return maximum if upper_bound is None else upper_bound
    return maximum


def _gauge(
    name: str,
    unit: Literal["ms", "By", "1"],
    latest: dict[str, LatestMetric],
    rows: tuple[MetricAggregate, ...],
) -> RuntimeGauge:
    """组合最新原始样本和聚合样本量。

    Args:
        name: 受控 gauge 名。
        unit: 页面展示单位。
        latest: 各指标最后一条原始样本。
        rows: 时间窗内 metric 聚合。

    Returns:
        单样本只标 partial 的最新值。
    """

    sample_size = sum(row.sample_count for row in rows if row.name == name)
    sample = latest.get(name)
    if sample is None:
        return RuntimeGauge(
            value=None,
            unit=unit,
            observed_at=None,
            sample_size=sample_size,
            quality="unavailable",
        )
    quality: StatisticsQuality = "reliable" if sample_size >= 2 else "partial"
    return RuntimeGauge(
        value=sample.value,
        unit=unit,
        observed_at=epoch_ns_to_datetime(sample.observed_at_ns),
        sample_size=max(sample_size, 1),
        quality=quality,
    )


def _metric_total(rows: tuple[MetricAggregate, ...], name: str) -> int:
    """累加一个 counter 指标在时间窗内的值。"""

    return round(sum(row.value_sum for row in rows if row.name == name))


def _latest_metric_int(latest: dict[str, LatestMetric], name: str) -> int:
    """把最新 gauge 值转换为非负整数；缺失时返回零。

    Args:
        latest: 各指标最后一条原始样本。
        name: 要读取的受控 gauge 名。

    Returns:
        最新值取整后的非负整数。
    """

    sample = latest.get(name)
    return max(round(sample.value), 0) if sample is not None else 0


def _gauge_series(
    rows: tuple[MetricAggregate, ...],
    name: str,
) -> list[RuntimeGaugePoint]:
    """把一个 gauge 的聚合桶转换成最小、最大和平均时序。

    Args:
        rows: 时间窗内 metric 聚合。
        name: 要转换的受控 gauge 名。

    Returns:
        按桶起点排序的时序点。
    """

    selected = sorted(
        (row for row in rows if row.name == name),
        key=lambda row: row.bucket_start_ns,
    )
    return [
        RuntimeGaugePoint(
            bucket_start=epoch_ns_to_datetime(row.bucket_start_ns),
            minimum=row.value_min,
            maximum=row.value_max,
            average=row.value_sum / row.sample_count,
            sample_size=row.sample_count,
        )
        for row in selected
        if row.sample_count > 0
    ]


def _span_count(rows: tuple[SpanAggregate, ...], operation: str) -> int:
    """累加一个 operation 的全部 span 样本数。"""

    return sum(row.sample_count for row in rows if row.operation == operation)


def _last_clean_exit(observation: RuntimeObservation) -> datetime | None:
    """返回时间窗内最后一次明确进程树归零的结束时刻。"""

    clean = [
        item
        for item in observation.latest_spans
        if item.operation == "desktop.exit" and item.status == "ok"
    ]
    if not clean:
        return None
    return epoch_ns_to_datetime(max(item.ended_at_ns for item in clean))


def _freshness(
    observation: RuntimeObservation,
    resolution: Literal["hour", "day"],
) -> SourceFreshness:
    """比较 raw 与目标聚合水位，说明页面是否已经追上最新事实。"""

    aggregate = observation.watermarks.get(resolution)
    if aggregate is None:
        return SourceFreshness(updated_at=None, status="unavailable")
    raw = observation.watermarks.get("raw")
    status: Literal["fresh", "stale", "unavailable"] = "fresh"
    if raw is not None and aggregate < raw:
        status = "stale"
    return SourceFreshness(updated_at=aggregate, status=status)


def _distribution_quality(sample_size: int) -> StatisticsQuality:
    """把样本量转换为分布质量；p95 门槛作为可靠线。"""

    if sample_size == 0:
        return "unavailable"
    if sample_size < 20:
        return "partial"
    return "reliable"


def _group_quality(rows: list[RuntimeDistribution]) -> StatisticsQuality:
    """返回一组 operation 中最有用且不掩盖缺失的质量。"""

    available = [row for row in rows if row.sample_size > 0]
    if not available:
        return "unavailable"
    if len(available) < len(rows) or any(
        row.quality == "partial" for row in available
    ):
        return "partial"
    return "reliable"


def _overall_quality(
    sample_size: int,
    snapshot: CollectorSnapshot,
    freshness: SourceFreshness,
) -> StatisticsQuality:
    """根据样本、丢弃和聚合水位确定整页质量。"""

    if sample_size == 0 or freshness.status == "unavailable":
        return "unavailable"
    if snapshot.dropped or snapshot.rejected or freshness.status == "stale":
        return "partial"
    return "reliable"


def _gaps(
    rss: RuntimeGauge,
    last_clean_exit_at: datetime | None,
    distributions: list[RuntimeDistribution],
    freshness: SourceFreshness,
) -> list[RuntimeGap]:
    """生成少量、稳定且可行动的数据缺口说明。"""

    gaps: list[RuntimeGap] = []
    if rss.sample_size == 0:
        gaps.append(RuntimeGap(code="rss_missing", message="当前时间窗没有 Sidecar RSS 采样。"))
    elif rss.sample_size == 1:
        gaps.append(RuntimeGap(code="rss_single_sample", message="RSS 只有一次采样，只展示事实，不判断上涨。"))
    if last_clean_exit_at is None:
        gaps.append(RuntimeGap(code="clean_exit_missing", message="当前时间窗没有可确认的干净退出。"))
    if any(0 < row.sample_size < 20 for row in distributions):
        gaps.append(RuntimeGap(code="p95_sample_short", message="部分操作少于 20 个样本，暂不展示 p95。"))
    if freshness.status == "stale":
        gaps.append(RuntimeGap(code="aggregate_stale", message="后台聚合尚未追上最新采集事实。"))
    return gaps
