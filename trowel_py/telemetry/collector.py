"""用有界单 writer 线程把业务热路径与 telemetry.db 隔离。"""

from __future__ import annotations

import logging
import queue
import sqlite3
import threading
import time
from collections import Counter, OrderedDict
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from types import TracebackType
from typing import Any, Protocol

from trowel_py.telemetry.contracts import (
    PreparedBatch,
    TelemetryBatchRequest,
    TelemetrySubmitResult,
    prepare_batch,
)

logger = logging.getLogger(__name__)


class TelemetryWriterPort(Protocol):
    """声明 collector 后台线程需要的数据库写操作。"""

    def __enter__(self) -> "TelemetryWriterPort":
        """在当前后台线程打开 writer 生命周期。"""

        ...

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """回滚未提交事务并关闭 writer。

        Args:
            exc_type: with 块抛出的异常类型。
            exc_value: with 块抛出的异常实例。
            traceback: with 块异常的调用栈。
        """

        ...

    def write_batches(self, batches: Sequence[PreparedBatch]) -> Any:
        """原子写入一个 flush 窗口中的批次。"""

        ...

    def aggregate(self, through: datetime) -> Any:
        """幂等生成小时和日聚合。"""

        ...

    def cleanup(self, now: datetime) -> Any:
        """按三层保留策略清理过期遥测。"""

        ...


@dataclass(frozen=True)
class CollectorSnapshot:
    """公开 collector 当前进程的有界运行状态。

    Attributes:
        accepted: 通过白名单并成功进入队列的记录总数。
        rejected: 因版本、schema、隐私或批次冲突拒绝的记录总数。
        dropped: 因队列满、关闭、数据库失败或关闭超时丢弃的记录总数。
        queued_records: 仍在队列中等待 writer 领取的记录数。
        inflight_records: writer 当前正在处理的记录数。
        running: writer 线程是否仍存活。
        accepting: collector 是否仍接受新批次。
        last_error_category: 最近一次后台失败的稳定类别。
    """

    accepted: int
    rejected: int
    dropped: int
    queued_records: int
    inflight_records: int
    running: bool
    accepting: bool
    last_error_category: str | None


@dataclass(frozen=True)
class CollectorCloseReport:
    """描述有界关闭是否完成 drain 以及累计丢弃数量。

    Attributes:
        drained: writer 是否在等待上限内退出。
        dropped: collector 当前进程累计丢弃记录数。
        remaining_records: 超时时仍在队列或 writer 中的记录数。
    """

    drained: bool
    dropped: int
    remaining_records: int


class TelemetryCollector:
    """提供不等待 SQLite 的提交入口，并由单后台线程串行写库。

    Attributes:
        queue_capacity: 队列按记录数计算的硬上限。
        flush_size: 普通写事务触发 flush 的记录上限。
        flush_interval_seconds: 小批次等待合并的最长秒数。
    """

    def __init__(
        self,
        writer_factory: Callable[[], TelemetryWriterPort],
        *,
        recent_batches: Mapping[str, bytes] | None = None,
        queue_capacity: int = 4096,
        flush_size: int = 250,
        flush_interval_seconds: float = 0.1,
        maintenance_interval_seconds: float = 300.0,
        busy_retries: int = 2,
        retry_base_seconds: float = 0.01,
        recent_batch_limit: int = 8192,
        checkpoint_requester: Callable[[], None] | None = None,
    ) -> None:
        """保存 writer 工厂和所有有界队列参数。

        Args:
            writer_factory: 后台线程启动后在该线程内创建 writer 的函数。
            recent_batches: 启动时从 telemetry.db 载入的批次指纹。
            queue_capacity: 尚未完成写入的最大记录数。
            flush_size: normal 批次累计达到该数量时立即写库。
            flush_interval_seconds: 首条记录进入 flush 后最多等待的秒数。
            maintenance_interval_seconds: 聚合和清理之间的最短秒数。
            busy_retries: SQLite busy 后额外重试次数。
            retry_base_seconds: 第一次 busy 重试前的退避秒数。
            recent_batch_limit: 内存中最多保留的批次身份数量。
            checkpoint_requester: 每次成功 flush 后唤醒独立 checkpointer 的函数。
        """

        if queue_capacity <= 0:
            raise ValueError("queue_capacity must be positive")
        if flush_size <= 0:
            raise ValueError("flush_size must be positive")
        if flush_interval_seconds <= 0:
            raise ValueError("flush_interval_seconds must be positive")
        if maintenance_interval_seconds <= 0:
            raise ValueError("maintenance_interval_seconds must be positive")
        self.queue_capacity = queue_capacity
        self.flush_size = flush_size
        self.flush_interval_seconds = flush_interval_seconds
        self._writer_factory = writer_factory
        self._maintenance_interval_seconds = maintenance_interval_seconds
        self._busy_retries = max(busy_retries, 0)
        self._retry_base_seconds = max(retry_base_seconds, 0.0)
        self._recent_batch_limit = max(recent_batch_limit, 1)
        self._checkpoint_requester = checkpoint_requester
        self._queue: queue.Queue[PreparedBatch] = queue.Queue(maxsize=queue_capacity)
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._accepting = False
        self._queued_records = 0
        self._inflight_records = 0
        self._accepted = 0
        self._rejected = 0
        self._dropped = 0
        self._last_error_category: str | None = None
        self._close_timeout_counted = False
        self._recent_batches: OrderedDict[str, bytes] = OrderedDict()
        for batch_id, fingerprint in (recent_batches or {}).items():
            self._remember_batch(batch_id, fingerprint)

    def start(self) -> None:
        """启动唯一 writer 线程；重复调用保持幂等。"""

        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            if self._stop.is_set():
                raise RuntimeError("telemetry collector cannot restart after close")
            self._accepting = True
            self._thread = threading.Thread(
                target=self._run,
                name="trowel-telemetry-writer",
                daemon=True,
            )
            self._thread.start()

    def submit(self, request: TelemetryBatchRequest) -> TelemetrySubmitResult:
        """校验并尝试入队，任何容量或生命周期失败都立即返回。

        Args:
            request: 版本化 span/metric 批次。

        Returns:
            本次接受、拒绝、丢弃和重复的确定计数。
        """

        prepared = prepare_batch(request)
        errors = Counter(prepared.error_categories)
        with self._lock:
            self._rejected += prepared.rejected_count
            existing = self._recent_batches.get(prepared.batch_id)
            if existing is not None:
                if existing == prepared.fingerprint:
                    return TelemetrySubmitResult(
                        accepted=0,
                        rejected=prepared.rejected_count,
                        dropped=0,
                        duplicate=True,
                        error_categories=dict(sorted(errors.items())),
                    )
                errors["batch_conflict"] += prepared.accepted_count
                self._rejected += prepared.accepted_count
                return TelemetrySubmitResult(
                    accepted=0,
                    rejected=prepared.rejected_count + prepared.accepted_count,
                    dropped=0,
                    duplicate=False,
                    error_categories=dict(sorted(errors.items())),
                )
            if prepared.accepted_count == 0:
                return TelemetrySubmitResult(
                    accepted=0,
                    rejected=prepared.rejected_count,
                    dropped=0,
                    duplicate=False,
                    error_categories=dict(sorted(errors.items())),
                )
            if not self._accepting:
                errors["collector_closed"] += prepared.accepted_count
                self._dropped += prepared.accepted_count
                return TelemetrySubmitResult(
                    accepted=0,
                    rejected=prepared.rejected_count,
                    dropped=prepared.accepted_count,
                    duplicate=False,
                    error_categories=dict(sorted(errors.items())),
                )
            if self._queued_records + prepared.accepted_count > self.queue_capacity:
                errors["queue_full"] += prepared.accepted_count
                self._dropped += prepared.accepted_count
                return TelemetrySubmitResult(
                    accepted=0,
                    rejected=prepared.rejected_count,
                    dropped=prepared.accepted_count,
                    duplicate=False,
                    error_categories=dict(sorted(errors.items())),
                )
            try:
                self._queue.put_nowait(prepared)
            except queue.Full:
                errors["queue_full"] += prepared.accepted_count
                self._dropped += prepared.accepted_count
                return TelemetrySubmitResult(
                    accepted=0,
                    rejected=prepared.rejected_count,
                    dropped=prepared.accepted_count,
                    duplicate=False,
                    error_categories=dict(sorted(errors.items())),
                )
            self._queued_records += prepared.accepted_count
            self._accepted += prepared.accepted_count
            self._remember_batch(prepared.batch_id, prepared.fingerprint)
        return TelemetrySubmitResult(
            accepted=prepared.accepted_count,
            rejected=prepared.rejected_count,
            dropped=0,
            duplicate=False,
            error_categories=dict(sorted(errors.items())),
        )

    def snapshot(self) -> CollectorSnapshot:
        """返回不含任何事件正文或身份的当前状态快照。"""

        with self._lock:
            thread = self._thread
            return CollectorSnapshot(
                accepted=self._accepted,
                rejected=self._rejected,
                dropped=self._dropped,
                queued_records=self._queued_records,
                inflight_records=self._inflight_records,
                running=bool(thread is not None and thread.is_alive()),
                accepting=self._accepting,
                last_error_category=self._last_error_category,
            )

    def close(self, *, timeout_seconds: float = 1.0) -> CollectorCloseReport:
        """停止接收并在上限内等待队列和 writer 收敛。

        Args:
            timeout_seconds: 最多等待 writer 线程退出的秒数。

        Returns:
            是否完成 drain、累计丢弃数和超时剩余记录数。
        """

        with self._lock:
            self._accepting = False
            thread = self._thread
        self._stop.set()
        if thread is not None:
            thread.join(max(timeout_seconds, 0.0))
        alive = bool(thread is not None and thread.is_alive())
        with self._lock:
            remaining = self._queued_records + self._inflight_records
            if alive and remaining and not self._close_timeout_counted:
                self._dropped += remaining
                self._last_error_category = "close_timeout"
                self._close_timeout_counted = True
            return CollectorCloseReport(
                drained=not alive,
                dropped=self._dropped,
                remaining_records=remaining if alive else 0,
            )

    def _run(self) -> None:
        """在线程内创建 writer，并持续 flush 到 stop 且队列为空。"""

        try:
            with self._writer_factory() as writer:
                # 首轮也等待完整间隔，避免应用启动时与业务库迁移集中争用 SQLite。
                last_maintenance = time.monotonic()
                while not self._stop.is_set() or not self._queue.empty():
                    batches = self._next_flush()
                    if batches:
                        succeeded = self._write_with_retry(writer, batches)
                        if succeeded:
                            self._request_checkpoint()
                        with self._lock:
                            batch_records = sum(item.accepted_count for item in batches)
                            self._inflight_records -= batch_records
                            if not succeeded and not self._close_timeout_counted:
                                self._dropped += batch_records
                                for batch in batches:
                                    if (
                                        self._recent_batches.get(batch.batch_id)
                                        == batch.fingerprint
                                    ):
                                        self._recent_batches.pop(batch.batch_id, None)
                    now = time.monotonic()
                    if now - last_maintenance >= self._maintenance_interval_seconds:
                        self._run_maintenance(writer)
                        self._request_checkpoint()
                        last_maintenance = now
        except BaseException:
            logger.warning("[telemetry] writer thread stopped", exc_info=True)
            with self._lock:
                remaining = self._queued_records + self._inflight_records
                if not self._close_timeout_counted:
                    self._dropped += remaining
                self._queued_records = 0
                self._inflight_records = 0
                self._last_error_category = "writer_stopped"
                self._accepting = False

    def _next_flush(self) -> list[PreparedBatch]:
        """最多等待一个 flush 窗口并领取一组批次。"""

        try:
            first = self._queue.get(timeout=min(self.flush_interval_seconds, 0.1))
        except queue.Empty:
            return []
        batches = [first]
        record_count = first.accepted_count
        deadline = time.monotonic() + self.flush_interval_seconds
        while record_count < self.flush_size and first.mode != "backfill":
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            try:
                candidate = self._queue.get(timeout=remaining)
            except queue.Empty:
                break
            if candidate.mode == "backfill" and batches:
                self._queue.put_nowait(candidate)
                break
            batches.append(candidate)
            record_count += candidate.accepted_count
        with self._lock:
            self._queued_records -= record_count
            self._inflight_records += record_count
        return batches

    def _write_with_retry(
        self,
        writer: TelemetryWriterPort,
        batches: Sequence[PreparedBatch],
    ) -> bool:
        """对 SQLite busy 做有限退避，其他数据库失败直接丢弃。"""

        for attempt in range(self._busy_retries + 1):
            try:
                writer.write_batches(batches)
                return True
            except sqlite3.OperationalError as exc:
                category = "database_busy" if "locked" in str(exc).lower() else "database_error"
                with self._lock:
                    self._last_error_category = category
                if category != "database_busy" or attempt >= self._busy_retries:
                    logger.warning("[telemetry] batch write failed: %s", category)
                    return False
                time.sleep(self._retry_base_seconds * (2**attempt))
            except Exception:
                logger.warning("[telemetry] batch write failed", exc_info=True)
                with self._lock:
                    self._last_error_category = "database_error"
                return False
        return False

    def _run_maintenance(self, writer: TelemetryWriterPort) -> None:
        """执行失败隔离的聚合和清理，不改变 collector 线程存活性。"""

        now = datetime.now(UTC)
        try:
            writer.aggregate(now)
            writer.cleanup(now)
        except Exception:
            logger.warning("[telemetry] maintenance failed", exc_info=True)
            with self._lock:
                self._last_error_category = "maintenance_error"

    def _request_checkpoint(self) -> None:
        """非阻塞唤醒独立 checkpointer，并隔离观测链自身的失败。"""

        if self._checkpoint_requester is None:
            return
        try:
            self._checkpoint_requester()
        except Exception:
            logger.warning("[telemetry] checkpoint request failed", exc_info=True)

    def _remember_batch(self, batch_id: str, fingerprint: bytes) -> None:
        """在固定容量的最近集合中保存批次身份和内容指纹。"""

        self._recent_batches[batch_id] = fingerprint
        self._recent_batches.move_to_end(batch_id)
        while len(self._recent_batches) > self._recent_batch_limit:
            self._recent_batches.popitem(last=False)
