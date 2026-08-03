"""定义 Statistics API 与遥测接收端共用的公开响应模型。"""

from __future__ import annotations

from datetime import datetime
from typing import Generic, Literal, TypeVar

from pydantic import BaseModel, ConfigDict

_Data = TypeVar("_Data")


class ApiEnvelope(BaseModel, Generic[_Data]):
    """统一表示 API 成功数据或错误文本。

    Attributes:
        success: 请求是否成功完成。
        data: 成功时的结构化数据；失败时为 None。
        error: 失败时的稳定错误文本；成功时为 None。
    """

    success: bool
    data: _Data | None
    error: str | None


class TelemetrySubmitData(BaseModel):
    """公开一次遥测提交的接受、拒绝和丢弃结果。

    Attributes:
        accepted: 已进入后端有界队列的记录数。
        rejected: 未通过 schema、版本或隐私校验的记录数。
        dropped: 因容量或生命周期没有进入队列的记录数。
        duplicate: 当前批次是否是已知幂等重试。
        error_categories: 各拒绝或丢弃类别对应的记录数。
    """

    accepted: int
    rejected: int
    dropped: int
    duplicate: bool
    error_categories: dict[str, int]


class SourceFreshness(BaseModel):
    """描述一个 Statistics 事实源最后处理到哪里。

    Attributes:
        updated_at: 来源最后处理到的事实时刻；从未产出时为 None。
        status: fresh 表示聚合追上 raw，stale 表示仍有未聚合事实。
    """

    updated_at: datetime | None
    status: Literal["fresh", "stale", "unavailable"]


class CollectorStatistics(BaseModel):
    """公开 collector 当前进程的有界状态和累计计数。

    Attributes:
        accepted: 已进入队列的记录总数。
        rejected: 白名单或批次冲突拒绝总数。
        dropped: 容量、数据库或关闭失败丢弃总数。
        queued_records: 仍在内存队列中的记录数。
        inflight_records: writer 正在处理的记录数。
        running: writer 线程是否存活。
        last_error_category: 最近后台失败分类；没有失败时为 None。
    """

    accepted: int
    rejected: int
    dropped: int
    queued_records: int
    inflight_records: int
    running: bool
    last_error_category: str | None


class SpanAggregateData(BaseModel):
    """公开一个小时或日的受控 span 耗时分布。

    Attributes:
        bucket_start: 当前小时或日期桶的 UTC 起点。
        component: 受控组件。
        operation: 受控操作名。
        status: 受控终态。
        runtime: 可选 runtime 维度。
        model: 可选模型维度。
        sample_count: 当前分组样本数。
        duration_sum_ms: 样本总耗时。
        duration_min_ms: 最短耗时。
        duration_max_ms: 最长耗时。
        histogram_counts: 固定非累计耗时 bucket 的样本数。
    """

    bucket_start: datetime
    component: str
    operation: str
    status: str
    runtime: str | None
    model: str | None
    sample_count: int
    duration_sum_ms: float
    duration_min_ms: float
    duration_max_ms: float
    histogram_counts: tuple[int, ...]


class MetricAggregateData(BaseModel):
    """公开一个小时或日的受控 metric 数值聚合。

    Attributes:
        bucket_start: 当前小时或日期桶的 UTC 起点。
        component: 受控组件。
        name: schema v1 指标名。
        kind: counter、gauge 或 histogram。
        unit: 次数、字节或毫秒。
        operation: 可选受控操作名。
        status: 受控终态。
        runtime: 可选 runtime 维度。
        model: 可选模型维度。
        sample_count: 当前分组样本数。
        value_sum: 样本值之和。
        value_min: 最小样本值。
        value_max: 最大样本值。
    """

    bucket_start: datetime
    component: str
    name: str
    kind: str
    unit: str
    operation: str | None
    status: str
    runtime: str | None
    model: str | None
    sample_count: int
    value_sum: float
    value_min: float
    value_max: float


class TelemetryStatisticsData(BaseModel):
    """汇总 Statistics API 公共元数据和遥测聚合结果。

    Attributes:
        generated_at: 本次响应生成时间。
        window_start: 查询包含的开始时刻。
        window_end: 查询不包含的结束时刻。
        timezone: 调用方选择的 IANA 时区。
        sample_size: 当前响应全部 span 与 metric 样本数。
        quality: reliable、partial 或 unavailable。
        freshness: 各事实源最后更新时间和状态。
        resolution: 当前返回小时还是日聚合。
        collector: collector 当前进程状态。
        database_bytes: 主库、WAL、SHM 和总占用字节数。
        histogram_upper_bounds_ms: span 非累计直方图 bucket 的毫秒上界。
        spans: 当前时间窗 span 聚合。
        metrics: 当前时间窗 metric 聚合。
    """

    model_config = ConfigDict(ser_json_timedelta="iso8601")

    generated_at: datetime
    window_start: datetime
    window_end: datetime
    timezone: str
    sample_size: int
    quality: Literal["reliable", "partial", "unavailable"]
    freshness: dict[str, SourceFreshness]
    resolution: Literal["hour", "day"]
    collector: CollectorStatistics
    database_bytes: dict[str, int]
    histogram_upper_bounds_ms: tuple[float | None, ...]
    spans: list[SpanAggregateData]
    metrics: list[MetricAggregateData]
