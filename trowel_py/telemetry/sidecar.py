"""按固定间隔采样 Python sidecar 的存活时长和当前 RSS。"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Protocol

import psutil  # type: ignore[import-untyped]

from trowel_py.telemetry.events import emit_metric
from trowel_py.telemetry.port import TelemetryPort

logger = logging.getLogger(__name__)


class ProcessMemoryPort(Protocol):
    """声明 sidecar 采样器需要的当前进程内存操作。"""

    def memory_info(self) -> object:
        """返回至少包含 rss 字节字段的进程内存快照。"""

        ...


class SidecarSampler:
    """在独立 asyncio task 中周期采样，不占用请求或退出关键路径。"""

    def __init__(
        self,
        port: TelemetryPort,
        *,
        interval_seconds: float = 30.0,
        process: ProcessMemoryPort | None = None,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        """保存非阻塞遥测端口、采样周期和当前进程句柄。

        Args:
            port: 当前应用持有的非阻塞遥测端口。
            interval_seconds: 两次采样之间的秒数，生产默认 30 秒。
            process: 测试可注入的进程内存来源；省略时读取当前进程。
            monotonic: 测试可注入的单调时钟。
        """

        self._port = port
        self._interval_seconds = max(interval_seconds, 0.1)
        self._process = process or psutil.Process()
        self._monotonic = monotonic
        self._started_at = monotonic()
        self._stop = asyncio.Event()
        self._task: asyncio.Task[None] | None = None

    def start(self) -> None:
        """幂等启动后台采样任务，不等待第一次采样。"""

        if self._task is not None:
            return
        self._task = asyncio.create_task(self._run(), name="telemetry-sidecar-sampler")

    async def close(self) -> None:
        """唤醒并等待采样任务结束，不取消正在提交的单条内存事实。"""

        self._stop.set()
        task = self._task
        if task is not None:
            await task

    def sample(self, observed_at: datetime | None = None) -> None:
        """读取一次当前 RSS 和 uptime，并分别提交两个 gauge。

        Args:
            observed_at: 测试可注入的带时区采样时刻。
        """

        stamp = observed_at or datetime.now(UTC)
        uptime_ms = max((self._monotonic() - self._started_at) * 1_000, 0.0)
        memory_info = self._process.memory_info()
        rss_bytes = float(getattr(memory_info, "rss"))
        emit_metric(
            self._port,
            component="sidecar",
            name="sidecar.uptime_ms",
            kind="gauge",
            unit="ms",
            value=uptime_ms,
            status="ok",
            operation="sidecar.sample",
            observed_at=stamp,
            attributes={"quality": "reliable", "sampled": True},
        )
        emit_metric(
            self._port,
            component="sidecar",
            name="sidecar.rss_bytes",
            kind="gauge",
            unit="By",
            value=rss_bytes,
            status="ok",
            operation="sidecar.sample",
            observed_at=stamp,
            attributes={"quality": "reliable", "sampled": True},
        )

    async def _run(self) -> None:
        """立即采样一次，之后用可唤醒等待维持固定周期。"""

        while not self._stop.is_set():
            try:
                self.sample()
            except Exception:
                # 采样失败不能终止应用；下一周期继续尝试。
                logger.debug("[telemetry] sidecar sample failed", exc_info=True)
            try:
                await asyncio.wait_for(
                    self._stop.wait(),
                    timeout=self._interval_seconds,
                )
            except TimeoutError:
                continue
