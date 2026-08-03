"""管理 telemetry.db 的连接、写入、聚合、清理和只读查询。"""

from __future__ import annotations

import hashlib
import sqlite3
from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import TracebackType
from typing import Literal, Sequence

from trowel_py.application_paths import resolve_application_data_root
from trowel_py.db.migrate import run_migrations
from trowel_py.telemetry.catalog import DURATION_HISTOGRAM_UPPER_BOUNDS_MS
from trowel_py.telemetry.contracts import PreparedBatch, datetime_to_epoch_ns

HOUR_NS = 3_600_000_000_000
DAY_NS = 86_400_000_000_000
RAW_RETENTION = timedelta(days=14)
HOURLY_RETENTION = timedelta(days=90)
DAILY_RETENTION = timedelta(days=730)
_HISTOGRAM_SLOT_BITS = 20
_HISTOGRAM_SLOT_MASK = (1 << _HISTOGRAM_SLOT_BITS) - 1


@dataclass(frozen=True)
class WriteReport:
    """汇总一次批量事务实际写入和幂等处理结果。

    Attributes:
        inserted_records: 新写入的 span 与 metric 行数。
        duplicate_batches: 已存在且指纹相同的批次数。
        conflicting_batches: 已存在但指纹不同的批次数。
    """

    inserted_records: int
    duplicate_batches: int
    conflicting_batches: int


@dataclass(frozen=True)
class AggregationReport:
    """记录聚合完成后四张主聚合表的总行数。

    Attributes:
        hourly_rows: 小时 span 聚合行数。
        daily_rows: 每日 span 聚合行数。
        hourly_metric_rows: 小时 metric 聚合行数。
        daily_metric_rows: 每日 metric 聚合行数。
    """

    hourly_rows: int
    daily_rows: int
    hourly_metric_rows: int
    daily_metric_rows: int


@dataclass(frozen=True)
class CleanupReport:
    """记录三层保留策略本次删除的行数。

    Attributes:
        raw_spans_deleted: 超过 14 天的原始 span 数。
        raw_metrics_deleted: 超过 14 天的原始 metric 数。
        hourly_rows_deleted: 超过 90 天的小时聚合主表行数。
        daily_rows_deleted: 超过 730 天的日聚合主表行数。
        batches_deleted: 后端接收超过 14 天的批次去重元数据数。
    """

    raw_spans_deleted: int
    raw_metrics_deleted: int
    hourly_rows_deleted: int
    daily_rows_deleted: int
    batches_deleted: int


@dataclass(frozen=True)
class SpanAggregate:
    """表示 Statistics API 可读取的一组 span 耗时分布。

    Attributes:
        bucket_start_ns: 小时或日期桶的 UTC Unix epoch 纳秒起点。
        component: 受控组件。
        operation: 受控操作名。
        status: 受控终态。
        runtime: 可选 runtime；空字符串表示该维度不适用。
        model: 可选模型；空字符串表示该维度不适用。
        sample_count: 当前分组的 span 数量。
        duration_sum_ms: 当前分组总耗时。
        duration_min_ms: 当前分组最短耗时。
        duration_max_ms: 当前分组最长耗时。
        histogram_counts: 与固定边界一一对应的非累计 bucket 数量。
    """

    bucket_start_ns: int
    component: str
    operation: str
    status: str
    runtime: str
    model: str
    sample_count: int
    duration_sum_ms: float
    duration_min_ms: float
    duration_max_ms: float
    histogram_counts: tuple[int, ...]


@dataclass(frozen=True)
class MetricAggregate:
    """表示 Statistics API 可读取的一组 metric 样本。

    Attributes:
        bucket_start_ns: 小时或日期桶的 UTC Unix epoch 纳秒起点。
        component: 受控组件。
        name: schema v1 指标名。
        kind: counter、gauge 或 histogram。
        unit: 指标单位。
        operation: 可选操作；空字符串表示不适用。
        status: 受控终态。
        runtime: 可选 runtime；空字符串表示不适用。
        model: 可选模型；空字符串表示不适用。
        sample_count: 当前分组的样本数量。
        value_sum: 当前分组数值之和。
        value_min: 当前分组最小值。
        value_max: 当前分组最大值。
    """

    bucket_start_ns: int
    component: str
    name: str
    kind: str
    unit: str
    operation: str
    status: str
    runtime: str
    model: str
    sample_count: int
    value_sum: float
    value_min: float
    value_max: float


@dataclass(frozen=True)
class LatestSpan:
    """表示 read model 可读取的最新受控 span 终态。

    Attributes:
        operation: 受控操作名。
        status: ok、error 或 unset。
        ended_at_ns: 操作结束的 UTC Unix epoch 纳秒。
        duration_ms: 该次操作的实际耗时。
    """

    operation: str
    status: str
    ended_at_ns: int
    duration_ms: float


@dataclass(frozen=True)
class LatestMetric:
    """表示 read model 可读取的最新受控指标样本。

    Attributes:
        name: 受控指标名。
        operation: 可选受控操作名；空字符串表示不适用。
        status: ok、error 或 unset。
        observed_at_ns: 采样时刻的 UTC Unix epoch 纳秒。
        value: 指标样本值。
    """

    name: str
    operation: str
    status: str
    observed_at_ns: int
    value: float


def resolve_telemetry_database_path(
    data_root: Path | None = None,
) -> Path:
    """返回独立 telemetry.db 的路径，不复用任何业务数据库。

    Args:
        data_root: 测试或离线工具明确指定的数据根；省略时使用应用数据根。

    Returns:
        数据根目录下的 ``telemetry.db``。
    """

    return (data_root or resolve_application_data_root()) / "telemetry.db"


class TelemetryDatabase:
    """创建线程内连接，并向写侧和读侧暴露独立入口。

    每次连接都在使用它的线程中创建。collector writer 长期留在后台线程；API reader
    使用短连接，因此不依赖 ``check_same_thread=False`` 掩盖所有权错误。

    Attributes:
        path: telemetry.db 的完整路径。
        busy_timeout_ms: 单次 SQLite 锁等待上限，超时由 collector 记为 dropped。
    """

    def __init__(self, path: Path, *, busy_timeout_ms: int = 100) -> None:
        """保存数据库路径和锁等待上限。

        Args:
            path: 独立 telemetry.db 文件路径。
            busy_timeout_ms: SQLite 遇到写锁时最多等待的毫秒数。
        """

        self.path = Path(path)
        self.busy_timeout_ms = busy_timeout_ms

    def initialize(self) -> None:
        """创建目录、应用独立迁移并立即关闭初始化连接。"""

        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = self._connect()
        try:
            migrations_dir = Path(__file__).parent / "migrations"
            run_migrations(connection, str(migrations_dir))
        finally:
            connection.close()

    def connect_reader(self) -> sqlite3.Connection:
        """创建供当前调用线程使用的短期只读逻辑连接。"""

        return self._connect()

    def open_writer(self) -> "TelemetryWriter":
        """在当前线程创建不承担 checkpoint 的长期 writer。

        自动 checkpoint 会让触发阈值的 COMMIT 同步承担磁盘归并，破坏 250 条事务
        的尾延迟预算。生产生命周期由独立 checkpointer 保持 WAL 有界。
        """

        return TelemetryWriter(self._connect(wal_autocheckpoint_pages=0))

    def reader(self) -> "TelemetryReader":
        """返回按查询打开短连接的线程安全读仓储。"""

        return TelemetryReader(self)

    def checkpoint(self) -> tuple[int, int, int]:
        """用独立连接执行不等待 reader 的 PASSIVE WAL checkpoint。

        Returns:
            SQLite 返回的 busy、WAL 总页数和已归并页数。
        """

        connection = self._connect(wal_autocheckpoint_pages=0)
        try:
            row = connection.execute("PRAGMA wal_checkpoint(PASSIVE)").fetchone()
            return (int(row[0]), int(row[1]), int(row[2]))
        finally:
            connection.close()

    def _connect(
        self,
        *,
        wal_autocheckpoint_pages: int = 1000,
    ) -> sqlite3.Connection:
        """用 WAL、NORMAL 和调用方指定的 checkpoint 策略打开连接。

        Args:
            wal_autocheckpoint_pages: 普通短连接使用 1000 页；长期 writer 传 0，
                由独立 checkpointer 承担归并和 fsync。
        """

        connection = sqlite3.connect(
            self.path,
            timeout=max(self.busy_timeout_ms, 0) / 1000,
            check_same_thread=True,
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=NORMAL")
        connection.execute(f"PRAGMA wal_autocheckpoint={wal_autocheckpoint_pages}")
        connection.execute(f"PRAGMA busy_timeout={max(self.busy_timeout_ms, 0)}")
        connection.execute("PRAGMA foreign_keys=ON")
        return connection


class TelemetryWriter(AbstractContextManager["TelemetryWriter"]):
    """在一个 collector 线程内串行提交写入、聚合和清理事务。"""

    def __init__(self, connection: sqlite3.Connection) -> None:
        """接管只能由当前线程使用的 SQLite 连接。

        Args:
            connection: 已应用 telemetry 连接级设置的 writer 连接。
        """

        self._connection = connection

    def __enter__(self) -> "TelemetryWriter":
        """返回当前 writer 供 collector 生命周期持有。"""

        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """结束未提交事务并关闭 writer 连接。

        Args:
            exc_type: with 块抛出的异常类型。
            exc_value: with 块抛出的异常实例。
            traceback: with 块异常的调用栈。
        """

        if self._connection.in_transaction:
            self._connection.rollback()
        self._connection.close()

    def write_batches(self, batches: Sequence[PreparedBatch]) -> WriteReport:
        """在一个事务中幂等写入一组不超过 flush 预算的批次。

        Args:
            batches: 已完成白名单校验且不含原始正文的批次。

        Returns:
            实际写入行数、重复批次和冲突批次数。
        """

        if not batches:
            return WriteReport(0, 0, 0)
        inserted_records = 0
        duplicate_batches = 0
        conflicting_batches = 0
        now_ns = datetime_to_epoch_ns(datetime.now(UTC))
        self._connection.execute("BEGIN IMMEDIATE")
        try:
            raw_watermark: int | None = None
            for batch in batches:
                cursor = self._connection.execute(
                    """
                    INSERT OR IGNORE INTO telemetry_batches (
                        batch_id, fingerprint, schema_version, source_component,
                        collected_at_ns, mode, accepted_count, rejected_count,
                        created_at_ns
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        batch.batch_id,
                        batch.fingerprint,
                        batch.schema_version,
                        batch.source_component,
                        batch.collected_at_ns,
                        batch.mode,
                        batch.accepted_count,
                        batch.rejected_count,
                        now_ns,
                    ),
                )
                if cursor.rowcount == 0:
                    existing = self._connection.execute(
                        "SELECT fingerprint FROM telemetry_batches WHERE batch_id=?",
                        (batch.batch_id,),
                    ).fetchone()
                    if existing is not None and bytes(existing["fingerprint"]) == batch.fingerprint:
                        duplicate_batches += 1
                    else:
                        conflicting_batches += 1
                    continue
                for span in batch.spans:
                    span_cursor = self._connection.execute(
                        """
                        INSERT OR IGNORE INTO raw_spans (
                            trace_id, span_id, parent_span_id,
                            started_at_ns, hour_start_ns, ended_at_ns, duration_ms,
                            duration_bucket, component,
                            operation, status, runtime, model, session_ref,
                            call_ref, attributes_json
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            span.trace_id,
                            span.span_id,
                            span.parent_span_id,
                            span.started_at_ns,
                            span.started_at_ns - (span.started_at_ns % HOUR_NS),
                            span.ended_at_ns,
                            span.duration_ms,
                            span.duration_bucket,
                            span.component,
                            span.operation,
                            span.status,
                            span.runtime or "",
                            span.model or "",
                            span.session_ref,
                            span.call_ref,
                            span.attributes_json,
                        ),
                    )
                    if span_cursor.rowcount == 0:
                        continue
                    inserted_records += 1
                    raw_watermark = max(raw_watermark or 0, span.ended_at_ns)
                    self._connection.executemany(
                        """
                        INSERT INTO span_links (
                            span_id, link_index, linked_trace_id,
                            linked_span_id
                        ) VALUES (?, ?, ?, ?)
                        """,
                        [
                            (
                                span.span_id,
                                index,
                                link.trace_id,
                                link.span_id,
                            )
                            for index, link in enumerate(span.links)
                        ],
                    )
                for metric in batch.metrics:
                    metric_cursor = self._connection.execute(
                        """
                        INSERT OR IGNORE INTO raw_metrics (
                            metric_key, observed_at_ns, component,
                            name, kind, unit, value, status, runtime, model,
                            operation, attributes_json
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            _metric_key(batch.batch_id, metric.metric_id),
                            metric.observed_at_ns,
                            metric.component,
                            metric.name,
                            metric.kind,
                            metric.unit,
                            metric.value,
                            metric.status,
                            metric.runtime,
                            metric.model,
                            metric.operation,
                            metric.attributes_json,
                        ),
                    )
                    if metric_cursor.rowcount == 0:
                        continue
                    inserted_records += 1
                    raw_watermark = max(raw_watermark or 0, metric.observed_at_ns)
            if raw_watermark is not None:
                self._upsert_watermark("raw", raw_watermark, now_ns)
            self._connection.commit()
        except BaseException:
            self._connection.rollback()
            raise
        return WriteReport(
            inserted_records=inserted_records,
            duplicate_batches=duplicate_batches,
            conflicting_batches=conflicting_batches,
        )

    def aggregate(self, through: datetime) -> AggregationReport:
        """依次推进互相独立的小时和日聚合水位。

        Args:
            through: 只纳入发生时间早于该时刻的事实。

        Returns:
            聚合完成后的主表总行数，重跑不会增加这些数量。
        """

        self.aggregate_hourly(through)
        return self.aggregate_daily(through)

    def aggregate_hourly(self, through: datetime) -> AggregationReport:
        """从现存 raw 幂等重算小时 span 与 metric 聚合。

        Args:
            through: 只纳入发生时间早于该时刻的原始事实。

        Returns:
            本阶段完成后四张主聚合表的总行数。
        """

        through_ns = datetime_to_epoch_ns(through)
        updated_at_ns = datetime_to_epoch_ns(datetime.now(UTC))
        self._connection.execute("BEGIN IMMEDIATE")
        try:
            self._aggregate_hourly_spans(through_ns)
            self._aggregate_hourly_metrics(through_ns)
            self._upsert_watermark("hour", through_ns, updated_at_ns)
            report = self._aggregation_report()
            self._connection.commit()
        except BaseException:
            self._connection.rollback()
            raise
        return report

    def aggregate_daily(self, through: datetime) -> AggregationReport:
        """从小时表幂等重算日 span 与 metric 聚合。

        Args:
            through: 只纳入桶起点早于该时刻的小时聚合。

        Returns:
            本阶段完成后四张主聚合表的总行数。
        """

        through_ns = datetime_to_epoch_ns(through)
        updated_at_ns = datetime_to_epoch_ns(datetime.now(UTC))
        self._connection.execute("BEGIN IMMEDIATE")
        try:
            self._aggregate_daily_spans(through_ns)
            self._aggregate_daily_metrics(through_ns)
            self._upsert_watermark("day", through_ns, updated_at_ns)
            report = self._aggregation_report()
            self._connection.commit()
        except BaseException:
            self._connection.rollback()
            raise
        return report

    def cleanup(self, now: datetime) -> CleanupReport:
        """按 14 天、90 天和 730 天三层策略分块删除过期遥测。

        删除只发生在当前 telemetry.db 连接，不执行 VACUUM，也不 ATTACH 业务库。

        Args:
            now: 计算三层保留截止点的当前时刻。

        Returns:
            各层主表和批次元数据实际删除的行数。
        """

        # 源表只删除完整聚合桶，避免重跑时用残缺小时或日期覆盖完整统计。
        raw_cutoff = _floor_epoch_ns(
            datetime_to_epoch_ns(now - RAW_RETENTION),
            HOUR_NS,
        )
        hourly_cutoff = _floor_epoch_ns(
            datetime_to_epoch_ns(now - HOURLY_RETENTION),
            DAY_NS,
        )
        daily_cutoff = _floor_epoch_ns(
            datetime_to_epoch_ns(now - DAILY_RETENTION),
            DAY_NS,
        )
        self._connection.execute("BEGIN IMMEDIATE")
        try:
            self._connection.execute(
                """
                DELETE FROM span_links
                WHERE span_id IN (
                    SELECT span_id FROM raw_spans
                    WHERE started_at_ns < ?
                )
                """,
                (raw_cutoff,),
            )
            raw_spans_deleted = self._delete_older_than(
                "raw_spans", "started_at_ns", raw_cutoff
            )
            raw_metrics_deleted = self._delete_older_than(
                "raw_metrics", "observed_at_ns", raw_cutoff
            )
            hourly_rows_deleted = self._delete_older_than(
                "hourly_span_stats", "bucket_start_ns", hourly_cutoff
            )
            hourly_rows_deleted += self._delete_older_than(
                "hourly_metric_stats", "bucket_start_ns", hourly_cutoff
            )
            daily_rows_deleted = self._delete_older_than(
                "daily_span_stats", "bucket_start_ns", daily_cutoff
            )
            daily_rows_deleted += self._delete_older_than(
                "daily_metric_stats", "bucket_start_ns", daily_cutoff
            )
            batch_cursor = self._connection.execute(
                """
                DELETE FROM telemetry_batches WHERE created_at_ns < ?
                """,
                (raw_cutoff,),
            )
            batches_deleted = max(batch_cursor.rowcount, 0)
            self._connection.commit()
        except BaseException:
            self._connection.rollback()
            raise
        return CleanupReport(
            raw_spans_deleted=raw_spans_deleted,
            raw_metrics_deleted=raw_metrics_deleted,
            hourly_rows_deleted=hourly_rows_deleted,
            daily_rows_deleted=daily_rows_deleted,
            batches_deleted=batches_deleted,
        )

    def _aggregate_hourly_spans(self, through_ns: int) -> None:
        """一次扫描现存 raw span，重算小时主表和固定直方图列。"""

        self._connection.execute(
            _span_stats_upsert_sql("hourly_span_stats", "raw_spans"),
            (through_ns,),
        )

    def _aggregate_daily_spans(self, through_ns: int) -> None:
        """从小时聚合重算日主表和可合并直方图列。"""

        self._connection.execute(
            _daily_span_stats_upsert_sql(),
            (through_ns,),
        )

    def _aggregate_hourly_metrics(self, through_ns: int) -> None:
        """从现存 raw metric 重算小时数值聚合。"""

        self._connection.execute(
            _metric_stats_upsert_sql("hourly_metric_stats", "raw_metrics", HOUR_NS),
            (through_ns,),
        )

    def _aggregate_daily_metrics(self, through_ns: int) -> None:
        """从小时 metric 聚合重算每日数值聚合。"""

        self._connection.execute(_daily_metric_stats_upsert_sql(), (through_ns,))

    def _upsert_watermark(
        self,
        stage: str,
        through_ns: int,
        updated_at_ns: int,
    ) -> None:
        """单调推进 raw、hour 或 day 的独立持久水位。

        Args:
            stage: 当前完成的持久阶段。
            through_ns: 当前阶段已经处理到的事实时间。
            updated_at_ns: 写入本次水位的墙钟时间。
        """

        self._connection.execute(
            """
            INSERT INTO telemetry_watermarks(stage, through_ns, updated_at_ns)
            VALUES (?, ?, ?)
            ON CONFLICT(stage) DO UPDATE SET
                through_ns = MAX(through_ns, excluded.through_ns),
                updated_at_ns = excluded.updated_at_ns
            """,
            (stage, through_ns, updated_at_ns),
        )

    def _delete_older_than(
        self,
        table: str,
        time_column: str,
        cutoff_ns: int,
    ) -> int:
        """删除一张内部表的过期行并返回总数。

        Args:
            table: 由本模块固定传入的内部表名。
            time_column: 由本模块固定传入的时间列名。
            cutoff_ns: 严格早于该时刻的行会被删除。
        """

        cursor = self._connection.execute(
            f"DELETE FROM {table} WHERE {time_column} < ?",
            (cutoff_ns,),
        )
        return max(cursor.rowcount, 0)

    def _table_count(self, table: str) -> int:
        """返回本模块固定内部表名的当前行数。"""

        return int(self._connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])

    def _aggregation_report(self) -> AggregationReport:
        """返回四张主聚合表的当前总行数。"""

        return AggregationReport(
            hourly_rows=self._table_count("hourly_span_stats"),
            daily_rows=self._table_count("daily_span_stats"),
            hourly_metric_rows=self._table_count("hourly_metric_stats"),
            daily_metric_rows=self._table_count("daily_metric_stats"),
        )


class TelemetryReader:
    """为 Statistics API 提供不共享 SQLite 连接的只读查询。"""

    def __init__(self, database: TelemetryDatabase) -> None:
        """保存每次查询创建短连接所需的数据库工厂。

        Args:
            database: 已初始化的 telemetry.db 所有者。
        """

        self._database = database

    def raw_counts(self) -> dict[str, int]:
        """返回当前原始 span 与 metric 行数。"""

        connection = self._database.connect_reader()
        try:
            return {
                "spans": int(connection.execute("SELECT COUNT(*) FROM raw_spans").fetchone()[0]),
                "metrics": int(connection.execute("SELECT COUNT(*) FROM raw_metrics").fetchone()[0]),
            }
        finally:
            connection.close()

    def watermarks(self) -> dict[str, datetime]:
        """返回 raw、hour、day 各自处理到的 UTC 时刻。"""

        connection = self._database.connect_reader()
        try:
            rows = connection.execute(
                "SELECT stage, through_ns FROM telemetry_watermarks"
            ).fetchall()
            return {
                str(row["stage"]): epoch_ns_to_datetime(int(row["through_ns"]))
                for row in rows
            }
        finally:
            connection.close()

    def recent_batch_fingerprints(self, *, limit: int = 8192) -> dict[str, bytes]:
        """加载最近批次身份，供 collector 在热路径识别重试。

        Args:
            limit: 启动时最多载入的批次数，避免历史元数据常驻内存。
        """

        connection = self._database.connect_reader()
        try:
            rows = connection.execute(
                """
                SELECT batch_id, fingerprint FROM telemetry_batches
                ORDER BY created_at_ns DESC LIMIT ?
                """,
                (limit,),
            ).fetchall()
            return {str(row["batch_id"]): bytes(row["fingerprint"]) for row in rows}
        finally:
            connection.close()

    def database_sizes(self) -> dict[str, int]:
        """返回主库、WAL、SHM 和合计占用字节数。"""

        path = self._database.path
        sizes = {
            "database": path.stat().st_size if path.exists() else 0,
            "wal": _file_size(path.with_name(path.name + "-wal")),
            "shm": _file_size(path.with_name(path.name + "-shm")),
        }
        sizes["total"] = sum(sizes.values())
        return sizes

    def query_span_aggregates(
        self,
        start: datetime,
        end: datetime,
        *,
        resolution: Literal["hour", "day"],
    ) -> list[SpanAggregate]:
        """读取时间窗内的 span 主聚合与对应可合并直方图。

        Args:
            start: 包含边界的查询开始时刻。
            end: 不包含边界的查询结束时刻。
            resolution: 读取小时或日聚合。

        Returns:
            按桶和低基数维度排序的 span 分布。
        """

        stats_table = _span_table(resolution)
        start_ns = datetime_to_epoch_ns(start)
        end_ns = datetime_to_epoch_ns(end)
        connection = self._database.connect_reader()
        try:
            rows = connection.execute(
                f"""
                SELECT * FROM {stats_table}
                WHERE bucket_start_ns >= ? AND bucket_start_ns < ?
                ORDER BY bucket_start_ns, component, operation, status, runtime, model
                """,
                (start_ns, end_ns),
            ).fetchall()
        finally:
            connection.close()
        histogram_reader = (
            _unpack_hourly_histogram
            if resolution == "hour"
            else _read_daily_histogram
        )
        return [
            SpanAggregate(
                bucket_start_ns=int(row["bucket_start_ns"]),
                component=str(row["component"]),
                operation=str(row["operation"]),
                status=str(row["status"]),
                runtime=str(row["runtime"]),
                model=str(row["model"]),
                sample_count=int(row["sample_count"]),
                duration_sum_ms=float(row["duration_sum_ms"]),
                duration_min_ms=float(row["duration_min_ms"]),
                duration_max_ms=float(row["duration_max_ms"]),
                histogram_counts=histogram_reader(row),
            )
            for row in rows
        ]

    def query_metric_aggregates(
        self,
        start: datetime,
        end: datetime,
        *,
        resolution: Literal["hour", "day"],
    ) -> list[MetricAggregate]:
        """读取时间窗内的 metric 数值聚合。

        Args:
            start: 包含边界的查询开始时刻。
            end: 不包含边界的查询结束时刻。
            resolution: 读取小时或日聚合。

        Returns:
            按桶和低基数维度排序的 metric 统计。
        """

        table = "hourly_metric_stats" if resolution == "hour" else "daily_metric_stats"
        connection = self._database.connect_reader()
        try:
            rows = connection.execute(
                f"""
                SELECT * FROM {table}
                WHERE bucket_start_ns >= ? AND bucket_start_ns < ?
                ORDER BY bucket_start_ns, component, name, operation, status, runtime, model
                """,
                (datetime_to_epoch_ns(start), datetime_to_epoch_ns(end)),
            ).fetchall()
        finally:
            connection.close()
        return [
            MetricAggregate(
                bucket_start_ns=int(row["bucket_start_ns"]),
                component=str(row["component"]),
                name=str(row["name"]),
                kind=str(row["kind"]),
                unit=str(row["unit"]),
                operation=str(row["operation"]),
                status=str(row["status"]),
                runtime=str(row["runtime"]),
                model=str(row["model"]),
                sample_count=int(row["sample_count"]),
                value_sum=float(row["value_sum"]),
                value_min=float(row["value_min"]),
                value_max=float(row["value_max"]),
            )
            for row in rows
        ]

    def latest_spans(
        self,
        operations: tuple[str, ...],
        start: datetime,
        end: datetime,
    ) -> list[LatestSpan]:
        """读取每个受控 operation 在时间窗内最后结束的一条 span。

        Args:
            operations: 调用方代码中固定声明的 operation 集合。
            start: 包含边界的查询开始时刻。
            end: 不包含边界的查询结束时刻。

        Returns:
            每个有样本 operation 的最后一条终态，不返回 attributes 或引用。
        """

        if not operations:
            return []
        placeholders = ",".join("?" for _ in operations)
        connection = self._database.connect_reader()
        try:
            rows = connection.execute(
                f"""
                SELECT operation, status, ended_at_ns, duration_ms
                FROM (
                    SELECT operation, status, ended_at_ns, duration_ms,
                           ROW_NUMBER() OVER (
                               PARTITION BY operation, status ORDER BY ended_at_ns DESC
                           ) AS rank
                    FROM raw_spans
                    WHERE operation IN ({placeholders})
                      AND started_at_ns >= ? AND started_at_ns < ?
                )
                WHERE rank = 1
                ORDER BY operation
                """,
                (*operations, datetime_to_epoch_ns(start), datetime_to_epoch_ns(end)),
            ).fetchall()
        finally:
            connection.close()
        return [
            LatestSpan(
                operation=str(row["operation"]),
                status=str(row["status"]),
                ended_at_ns=int(row["ended_at_ns"]),
                duration_ms=float(row["duration_ms"]),
            )
            for row in rows
        ]

    def latest_metrics(
        self,
        names: tuple[str, ...],
        start: datetime,
        end: datetime,
    ) -> list[LatestMetric]:
        """读取每个受控指标在时间窗内最后采到的一条原始样本。

        Args:
            names: 调用方代码中固定声明的指标名集合。
            start: 包含边界的查询开始时刻。
            end: 不包含边界的查询结束时刻。

        Returns:
            每个有样本指标的最后一条数值，不返回 attributes。
        """

        if not names:
            return []
        placeholders = ",".join("?" for _ in names)
        connection = self._database.connect_reader()
        try:
            rows = connection.execute(
                f"""
                SELECT name, operation, status, observed_at_ns, value
                FROM (
                    SELECT name, operation, status, observed_at_ns, value,
                           ROW_NUMBER() OVER (
                               PARTITION BY name ORDER BY observed_at_ns DESC
                           ) AS rank
                    FROM raw_metrics
                    WHERE name IN ({placeholders})
                      AND observed_at_ns >= ? AND observed_at_ns < ?
                )
                WHERE rank = 1
                ORDER BY name
                """,
                (*names, datetime_to_epoch_ns(start), datetime_to_epoch_ns(end)),
            ).fetchall()
        finally:
            connection.close()
        return [
            LatestMetric(
                name=str(row["name"]),
                operation=str(row["operation"]),
                status=str(row["status"]),
                observed_at_ns=int(row["observed_at_ns"]),
                value=float(row["value"]),
            )
            for row in rows
        ]


def epoch_ns_to_datetime(value: int) -> datetime:
    """把 UTC Unix epoch 纳秒还原为带时区 datetime。"""

    seconds, nanoseconds = divmod(value, 1_000_000_000)
    return datetime.fromtimestamp(seconds, tz=UTC).replace(
        microsecond=nanoseconds // 1_000
    )


def _floor_epoch_ns(value: int, period_ns: int) -> int:
    """把时间截到完整聚合桶起点，供保留策略避免删除半个源桶。"""

    return value - (value % period_ns)


def _file_size(path: Path) -> int:
    """返回可选数据库附属文件的当前字节数。"""

    return path.stat().st_size if path.exists() else 0


def _span_table(resolution: str) -> str:
    """把公开分辨率映射为固定表名，避免动态 SQL 接收任意标识符。"""

    if resolution == "hour":
        return "hourly_span_stats"
    if resolution == "day":
        return "daily_span_stats"
    raise ValueError(f"unsupported resolution: {resolution}")


def _span_stats_upsert_sql(table: str, source: str) -> str:
    """生成 raw span 的小时统计和五列精确压缩 histogram SQL。"""

    packed_columns = ", ".join(f"histogram_p{index}" for index in range(5))
    packed_selects = ", ".join(_packed_histogram_selects())
    packed_updates = ", ".join(
        f"histogram_p{index}=excluded.histogram_p{index}" for index in range(5)
    )

    return f"""
        INSERT INTO {table} (
            bucket_start_ns, component, operation, status, runtime, model,
            sample_count, duration_sum_ms, duration_min_ms, duration_max_ms,
            {packed_columns}
        )
        SELECT
            hour_start_ns,
            component, operation, status, runtime,
            model, COUNT(*), SUM(duration_ms),
            MIN(duration_ms), MAX(duration_ms), {packed_selects}
        FROM {source}
        WHERE started_at_ns < ?
        GROUP BY 1, 2, 3, 4, 5, 6
        ON CONFLICT(bucket_start_ns, component, operation, status, runtime, model)
        DO UPDATE SET
            sample_count=excluded.sample_count,
            duration_sum_ms=excluded.duration_sum_ms,
            duration_min_ms=excluded.duration_min_ms,
            duration_max_ms=excluded.duration_max_ms,
            {packed_updates}
    """


def _daily_span_stats_upsert_sql() -> str:
    """生成小时 span 主聚合到每日主聚合的固定 SQL。"""

    daily_columns = ", ".join(_daily_histogram_columns())
    daily_selects = ", ".join(_daily_histogram_selects())
    daily_updates = ", ".join(
        f"{column}=excluded.{column}" for column in _daily_histogram_columns()
    )

    return f"""
        INSERT INTO daily_span_stats (
            bucket_start_ns, component, operation, status, runtime, model,
            sample_count, duration_sum_ms, duration_min_ms, duration_max_ms,
            {daily_columns}
        )
        SELECT
            bucket_start_ns - (bucket_start_ns % {DAY_NS}),
            component, operation, status, runtime, model,
            SUM(sample_count), SUM(duration_sum_ms), MIN(duration_min_ms),
            MAX(duration_max_ms), {daily_selects}
        FROM hourly_span_stats
        WHERE bucket_start_ns < ?
        GROUP BY 1, 2, 3, 4, 5, 6
        ON CONFLICT(bucket_start_ns, component, operation, status, runtime, model)
        DO UPDATE SET
            sample_count=excluded.sample_count,
            duration_sum_ms=excluded.duration_sum_ms,
            duration_min_ms=excluded.duration_min_ms,
            duration_max_ms=excluded.duration_max_ms,
            {daily_updates}
    """


def _metric_key(batch_id: str, metric_id: str) -> bytes:
    """把批次和批内 metric 身份组合成固定宽度幂等键。"""

    return hashlib.blake2b(
        f"{batch_id}\0{metric_id}".encode("utf-8"),
        digest_size=16,
    ).digest()


def _packed_histogram_selects() -> tuple[str, ...]:
    """返回五个把每三个 bucket 编进 20 位槽的 SQLite 聚合表达式。"""

    expressions: list[str] = []
    for pack in range(5):
        first_bucket = pack * 3
        expressions.append(
            "SUM(CASE duration_bucket "
            f"WHEN {first_bucket} THEN 1 "
            f"WHEN {first_bucket + 1} THEN {1 << _HISTOGRAM_SLOT_BITS} "
            f"WHEN {first_bucket + 2} THEN {1 << (_HISTOGRAM_SLOT_BITS * 2)} "
            "ELSE 0 END)"
        )
    return tuple(expressions)


def _daily_histogram_columns() -> tuple[str, ...]:
    """返回每日聚合保存的 15 个普通 bucket 列。"""

    return tuple(
        f"duration_b{index:02d}"
        for index in range(len(DURATION_HISTOGRAM_UPPER_BOUNDS_MS))
    )


def _daily_histogram_selects() -> tuple[str, ...]:
    """返回解开小时 20 位槽并按日求和的表达式。"""

    expressions: list[str] = []
    for bucket in range(len(DURATION_HISTOGRAM_UPPER_BOUNDS_MS)):
        pack, slot = divmod(bucket, 3)
        shifted = f"(histogram_p{pack} >> {slot * _HISTOGRAM_SLOT_BITS})"
        expressions.append(f"SUM({shifted} & {_HISTOGRAM_SLOT_MASK})")
    return tuple(expressions)


def _unpack_hourly_histogram(row: sqlite3.Row) -> tuple[int, ...]:
    """把五个 SQLite 整数还原为 15 个小时 bucket 计数。"""

    counts: list[int] = []
    for bucket in range(len(DURATION_HISTOGRAM_UPPER_BOUNDS_MS)):
        pack, slot = divmod(bucket, 3)
        encoded = int(row[f"histogram_p{pack}"])
        counts.append(
            (encoded >> (slot * _HISTOGRAM_SLOT_BITS)) & _HISTOGRAM_SLOT_MASK
        )
    return tuple(counts)


def _read_daily_histogram(row: sqlite3.Row) -> tuple[int, ...]:
    """读取每日聚合的 15 个普通 bucket 计数。"""

    return tuple(int(row[column]) for column in _daily_histogram_columns())


def _metric_stats_upsert_sql(table: str, source: str, period_ns: int) -> str:
    """生成 raw metric 到小时数值聚合的固定 SQL。"""

    return f"""
        INSERT INTO {table} (
            bucket_start_ns, component, name, kind, unit, operation, status,
            runtime, model, sample_count, value_sum, value_min, value_max
        )
        SELECT
            observed_at_ns - (observed_at_ns % {period_ns}),
            component, name, kind, unit, COALESCE(operation, ''), status,
            COALESCE(runtime, ''), COALESCE(model, ''), COUNT(*), SUM(value),
            MIN(value), MAX(value)
        FROM {source}
        WHERE observed_at_ns < ?
        GROUP BY 1, 2, 3, 4, 5, 6, 7, 8, 9
        ON CONFLICT(
            bucket_start_ns, component, name, kind, unit, operation, status,
            runtime, model
        ) DO UPDATE SET
            sample_count=excluded.sample_count,
            value_sum=excluded.value_sum,
            value_min=excluded.value_min,
            value_max=excluded.value_max
    """


def _daily_metric_stats_upsert_sql() -> str:
    """生成小时 metric 聚合到每日数值聚合的固定 SQL。"""

    return f"""
        INSERT INTO daily_metric_stats (
            bucket_start_ns, component, name, kind, unit, operation, status,
            runtime, model, sample_count, value_sum, value_min, value_max
        )
        SELECT
            bucket_start_ns - (bucket_start_ns % {DAY_NS}),
            component, name, kind, unit, operation, status, runtime, model,
            SUM(sample_count), SUM(value_sum), MIN(value_min), MAX(value_max)
        FROM hourly_metric_stats
        WHERE bucket_start_ns < ?
        GROUP BY 1, 2, 3, 4, 5, 6, 7, 8, 9
        ON CONFLICT(
            bucket_start_ns, component, name, kind, unit, operation, status,
            runtime, model
        ) DO UPDATE SET
            sample_count=excluded.sample_count,
            value_sum=excluded.value_sum,
            value_min=excluded.value_min,
            value_max=excluded.value_max
    """
