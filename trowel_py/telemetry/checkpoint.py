"""在独立线程合并 WAL checkpoint 请求，避免拖慢 writer COMMIT。"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CheckpointCloseReport:
    """描述 checkpointer 的请求处理和有界关闭结果。

    Attributes:
        closed: 独立线程是否在等待上限内退出。
        requested: collector 发出的 checkpoint 请求总数。
        completed: 实际完成的合并 checkpoint 次数。
        failed: checkpoint 调用失败次数。
    """

    closed: bool
    requested: int
    completed: int
    failed: int


class TelemetryCheckpointer:
    """把连续请求合并成独立线程中的 SQLite PASSIVE checkpoint。"""

    def __init__(self, checkpoint: Callable[[], tuple[int, int, int]]) -> None:
        """保存一次 checkpoint 操作。

        Args:
            checkpoint: 打开独立连接并执行 PASSIVE checkpoint 的函数。
        """

        self._checkpoint = checkpoint
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._pending = False
        self._requested = 0
        self._completed = 0
        self._failed = 0

    def start(self) -> None:
        """启动独立线程；重复调用保持幂等。"""

        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            if self._stop.is_set():
                raise RuntimeError("telemetry checkpointer cannot restart after close")
            self._thread = threading.Thread(
                target=self._run,
                name="trowel-telemetry-checkpointer",
                daemon=True,
            )
            self._thread.start()

    def request(self) -> None:
        """记录一次非阻塞请求，并唤醒或复用当前 checkpoint。"""

        with self._lock:
            if self._stop.is_set():
                return
            self._requested += 1
            self._pending = True
        self._wake.set()

    def close(self, *, timeout_seconds: float = 1.0) -> CheckpointCloseReport:
        """停止接受请求并在上限内等待当前 checkpoint 完成。

        Args:
            timeout_seconds: 独立线程最多占用退出链的秒数。

        Returns:
            是否关闭以及请求、完成和失败计数。
        """

        self._stop.set()
        self._wake.set()
        with self._lock:
            thread = self._thread
        if thread is not None:
            thread.join(max(timeout_seconds, 0.0))
        with self._lock:
            return CheckpointCloseReport(
                closed=not bool(thread is not None and thread.is_alive()),
                requested=self._requested,
                completed=self._completed,
                failed=self._failed,
            )

    def _run(self) -> None:
        """消费合并后的 pending 标记，直到关闭且没有待处理请求。"""

        while True:
            self._wake.wait(0.1)
            self._wake.clear()
            with self._lock:
                pending = self._pending
                self._pending = False
                stopping = self._stop.is_set()
            if pending:
                try:
                    self._checkpoint()
                except Exception:
                    logger.warning("[telemetry] checkpoint failed", exc_info=True)
                    with self._lock:
                        self._failed += 1
                else:
                    with self._lock:
                        self._completed += 1
            with self._lock:
                if stopping and not self._pending:
                    return
