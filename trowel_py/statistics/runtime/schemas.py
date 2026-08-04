"""定义运行统计页使用的公开响应模型。"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel

from trowel_py.statistics.schemas import SourceFreshness

StatisticsQuality = Literal["reliable", "partial", "unavailable"]


class RuntimeDistribution(BaseModel):
    """描述一个受控运行操作的耗时与错误分布。

    Attributes:
        operation: 不含动态路径或身份的稳定操作名。
        label: 前端直接展示的大白话名称。
        sample_size: 时间窗内全部终态样本数。
        error_count: status 为 error 的样本数。
        p50_ms: 样本至少 5 个时的中位耗时，否则为 None。
        p95_ms: 样本至少 20 个时的尾部耗时，否则为 None。
        p99_ms: 样本至少 100 个时的尾部耗时，否则为 None。
        quality: 当前操作的数据可用程度。
    """

    operation: str
    label: str
    sample_size: int
    error_count: int
    p50_ms: float | None
    p95_ms: float | None
    p99_ms: float | None
    quality: StatisticsQuality


class RuntimeGauge(BaseModel):
    """描述一个最新值及其采样基础。

    Attributes:
        value: 最新样本值；没有事实时为 None。
        unit: 指标使用的稳定单位。
        observed_at: 最新样本时刻；没有事实时为 None。
        sample_size: 时间窗内同指标样本数。
        quality: 单样本只标 partial，不推断趋势或异常。
    """

    value: float | None
    unit: Literal["ms", "By", "1"]
    observed_at: datetime | None
    sample_size: int
    quality: StatisticsQuality


class RuntimeGaugePoint(BaseModel):
    """描述一个聚合时间桶中的 gauge 变化范围。

    Attributes:
        bucket_start: 当前 UTC 小时或日期桶起点。
        minimum: 桶内最小样本值。
        maximum: 桶内最大样本值。
        average: 桶内样本平均值。
        sample_size: 桶内采样次数。
    """

    bucket_start: datetime
    minimum: float
    maximum: float
    average: float
    sample_size: int


class SidecarStatistics(BaseModel):
    """汇总 Python sidecar 的存活、内存和异常事实。

    Attributes:
        uptime: 最新存活时长。
        rss: 最新常驻内存采样。
        restart_count: 时间窗内启动后的重启次数。
        abnormal_exit_count: 时间窗内非预期退出次数。
        rss_series: 按小时或日聚合的 RSS 最小、最大和平均值。
    """

    uptime: RuntimeGauge
    rss: RuntimeGauge
    restart_count: int
    abnormal_exit_count: int
    rss_series: list[RuntimeGaugePoint]


class RuntimeConnectionStatistics(BaseModel):
    """汇总 SSE 的建连、断线、重连和首事件事实。

    Attributes:
        connect_count: 时间窗内建连次数。
        disconnect_count: 客户端断开或传输异常次数。
        reconnect_count: 同一前端生命周期再次建连次数。
        operations: SSE 各受控阶段的耗时分布。
        quality: 当前 SSE 事实整体可用程度。
    """

    connect_count: int
    disconnect_count: int
    reconnect_count: int
    operations: list[RuntimeDistribution]
    quality: StatisticsQuality


class DatabaseFileStatistics(BaseModel):
    """描述一份业务数据库及 WAL/SHM 的当前体积。

    Attributes:
        name: 只含文件名的受控数据库名称。
        owner: 负责写入该库的领域模块。
        database_bytes: 主数据库文件大小。
        wal_bytes: WAL 文件大小。
        shm_bytes: SHM 文件大小。
        total_bytes: 三者当前总大小。
        quality: 文件路径可解析时为 reliable，否则 unavailable。
    """

    name: Literal["sessions.db", "workspaces.db", "telemetry.db"]
    owner: str
    database_bytes: int
    wal_bytes: int
    shm_bytes: int
    total_bytes: int
    quality: StatisticsQuality


class SQLiteStatistics(BaseModel):
    """汇总受控 SQLite 逻辑操作、锁错误和文件体积。

    Attributes:
        busy_count: SQLITE_BUSY 分类数量。
        locked_count: SQLITE_LOCKED 分类数量。
        operations: 只按稳定逻辑操作名分组的耗时分布。
        files: 不暴露绝对路径的数据库文件快照。
        quality: 当前 SQLite 事实整体可用程度。
    """

    busy_count: int
    locked_count: int
    operations: list[RuntimeDistribution]
    files: list[DatabaseFileStatistics]
    quality: StatisticsQuality


class RuntimeGap(BaseModel):
    """解释运行页没有显示某个数字的稳定原因。

    Attributes:
        code: 前端可稳定识别的缺口代码。
        message: 不含异常正文或路径的大白话说明。
    """

    code: str
    message: str


class RuntimeStatisticsData(BaseModel):
    """汇总桌面生命周期、服务、连接、数据库和资源 owner 统计。

    Attributes:
        generated_at: 本次 read model 生成时刻。
        window_start: 查询包含的开始时刻。
        window_end: 查询不包含的结束时刻。
        timezone: 调用方选择的 IANA 时区。
        sample_size: 参与本页的 span 与 metric 聚合样本数。
        quality: 整页来源质量。
        freshness: 遥测聚合事实源的新鲜度。
        resolution: 当前读取小时还是日聚合。
        sidecar: sidecar 存活和内存摘要。
        last_clean_exit_at: 当前时间窗最后一次进程树归零时刻。
        lifecycle: 启动、关闭、退出和恢复操作分布。
        fastapi: 受控 FastAPI 路由组分布。
        sse: SSE 连接阶段统计。
        sqlite: SQLite 逻辑操作与文件统计。
        resources: 四层资源 owner 的关闭分布。
        resource_remaining_count: 当前时间窗最后一次 owner 或恢复核验的残留数量。
        gaps: 页面明确展示的数据缺口。
    """

    generated_at: datetime
    window_start: datetime
    window_end: datetime
    timezone: str
    sample_size: int
    quality: StatisticsQuality
    freshness: dict[str, SourceFreshness]
    resolution: Literal["hour", "day"]
    sidecar: SidecarStatistics
    last_clean_exit_at: datetime | None
    lifecycle: list[RuntimeDistribution]
    fastapi: list[RuntimeDistribution]
    sse: RuntimeConnectionStatistics
    sqlite: SQLiteStatistics
    resources: list[RuntimeDistribution]
    resource_remaining_count: int
    gaps: list[RuntimeGap]
