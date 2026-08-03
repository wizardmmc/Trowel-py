"""组合 telemetry reader 与受控数据库路径，供运行统计只读查询。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal, Protocol

from trowel_py.statistics.runtime.schemas import DatabaseFileStatistics
from trowel_py.telemetry.storage import (
    LatestMetric,
    LatestSpan,
    MetricAggregate,
    SpanAggregate,
)


class TelemetryRuntimeReaderPort(Protocol):
    """声明运行统计依赖的通用遥测只读操作。"""

    def watermarks(self) -> dict[str, datetime]:
        """返回 raw、hour 和 day 聚合水位。"""

        ...

    def query_span_aggregates(
        self,
        start: datetime,
        end: datetime,
        *,
        resolution: Literal["hour", "day"],
    ) -> list[SpanAggregate]:
        """读取时间窗内 span 聚合。"""

        ...

    def query_metric_aggregates(
        self,
        start: datetime,
        end: datetime,
        *,
        resolution: Literal["hour", "day"],
    ) -> list[MetricAggregate]:
        """读取时间窗内 metric 聚合。"""

        ...

    def latest_spans(
        self,
        operations: tuple[str, ...],
        start: datetime,
        end: datetime,
    ) -> list[LatestSpan]:
        """读取每个受控 operation 的最新 span。"""

        ...

    def latest_metrics(
        self,
        names: tuple[str, ...],
        start: datetime,
        end: datetime,
    ) -> list[LatestMetric]:
        """读取每个受控指标的最新样本。"""

        ...


@dataclass(frozen=True)
class RuntimeObservation:
    """保存运行页一次查询需要的全部无正文事实。

    Attributes:
        spans: 聚合 span 行。
        metrics: 聚合 metric 行。
        latest_spans: 各关键 operation 的最新终态。
        latest_metrics: 各关键 gauge 的最新样本。
        watermarks: 遥测各阶段水位。
        files: 三份受控数据库的当前文件体积。
    """

    spans: tuple[SpanAggregate, ...]
    metrics: tuple[MetricAggregate, ...]
    latest_spans: tuple[LatestSpan, ...]
    latest_metrics: tuple[LatestMetric, ...]
    watermarks: dict[str, datetime]
    files: tuple[DatabaseFileStatistics, ...]


class RuntimeStatisticsReader:
    """从遥测库和固定数据库路径读取运行页事实。"""

    def __init__(
        self,
        telemetry: TelemetryRuntimeReaderPort,
        database_paths: dict[str, tuple[Path, str]],
    ) -> None:
        """保存通用遥测 reader 和固定文件名到 owner 的映射。

        Args:
            telemetry: 每次查询打开短 SQLite 连接的通用 reader。
            database_paths: 受控文件名到本地路径、领域 owner 的映射。
        """

        self._telemetry = telemetry
        self._database_paths = dict(database_paths)

    def read(
        self,
        start: datetime,
        end: datetime,
        *,
        resolution: Literal["hour", "day"],
    ) -> RuntimeObservation:
        """读取一次运行页需要的聚合、最新值和文件快照。

        Args:
            start: 包含边界的时间窗起点。
            end: 不包含边界的时间窗终点。
            resolution: 聚合读取小时或日表。

        Returns:
            不包含 attributes、原始身份或绝对路径的事实集合。
        """

        latest_operations = ("desktop.exit",)
        latest_metric_names = (
            "sidecar.uptime_ms",
            "sidecar.rss_bytes",
            "resource.remaining",
        )
        return RuntimeObservation(
            spans=tuple(
                self._telemetry.query_span_aggregates(
                    start,
                    end,
                    resolution=resolution,
                )
            ),
            metrics=tuple(
                self._telemetry.query_metric_aggregates(
                    start,
                    end,
                    resolution=resolution,
                )
            ),
            latest_spans=tuple(
                self._telemetry.latest_spans(latest_operations, start, end)
            ),
            latest_metrics=tuple(
                self._telemetry.latest_metrics(latest_metric_names, start, end)
            ),
            watermarks=self._telemetry.watermarks(),
            files=tuple(self._read_files()),
        )

    def _read_files(self) -> list[DatabaseFileStatistics]:
        """读取受控数据库及其 WAL/SHM 体积，不公开本地路径。"""

        files: list[DatabaseFileStatistics] = []
        for name in ("sessions.db", "workspaces.db", "telemetry.db"):
            configured = self._database_paths.get(name)
            if configured is None:
                files.append(
                    DatabaseFileStatistics(
                        name=name,
                        owner="unavailable",
                        database_bytes=0,
                        wal_bytes=0,
                        shm_bytes=0,
                        total_bytes=0,
                        quality="unavailable",
                    )
                )
                continue
            path, owner = configured
            database_bytes = _file_size(path)
            wal_bytes = _file_size(path.with_name(path.name + "-wal"))
            shm_bytes = _file_size(path.with_name(path.name + "-shm"))
            files.append(
                DatabaseFileStatistics(
                    name=name,
                    owner=owner,
                    database_bytes=database_bytes,
                    wal_bytes=wal_bytes,
                    shm_bytes=shm_bytes,
                    total_bytes=database_bytes + wal_bytes + shm_bytes,
                    quality="reliable",
                )
            )
        return files


def _file_size(path: Path) -> int:
    """返回文件当前字节数；缺失文件按零处理。

    Args:
        path: 已由应用组装的受控数据库或附属文件路径。

    Returns:
        文件大小；不存在时为 0。
    """

    try:
        return path.stat().st_size
    except OSError:
        return 0
