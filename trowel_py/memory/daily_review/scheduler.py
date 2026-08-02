"""在应用生命周期内处理会话关闭请求，并调度每日 Memory review。"""

from __future__ import annotations

import asyncio
import logging
import threading
import tomllib
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from pathlib import Path
from typing import Any, Awaitable, Callable

from trowel_py.agent_host.binding import SessionBinding
from trowel_py.memory import paths
from trowel_py.memory.daily_review.requests import (
    enqueue_session_review,
    load_session_review_requests,
)
from trowel_py.memory.sessions_repo import ReviewRequest
from trowel_py.resource_lifecycle.registry import ResourceRegistry
from trowel_py.memory.scheduling import seconds_until

logger = logging.getLogger("trowel_py.memory.review_scheduler")

DEFAULT_REVIEW_TIME: time = time(2, 30)
DEFAULT_REVIEW_ENABLED: bool = True
IMMEDIATE_RETRY_MIN_SECONDS = 1.0
IMMEDIATE_RETRY_MAX_SECONDS = 300.0
DispatchFn = Callable[[dict[str, Any]], None]
NowFn = Callable[[], datetime]
SleepFn = Callable[[float], Awaitable[None]]

# dispatch 在线程中运行，注册检查必须加锁，不能依赖进程级 bool 的原子性。
_REG_LOCK = threading.Lock()
_REVIEW_JOB_REGISTERED = False


@dataclass(frozen=True)
class ReviewScheduleConfig:
    """记录 Daily review 的启用状态和每日本地触发时刻。

    Attributes:
        review_time: 每天按本地时钟触发 review 的时刻。
        review_enabled: 是否在应用启动时启用补跑和每日循环。
    """

    review_time: time
    review_enabled: bool


def _parse_time(raw: str | None) -> time:
    """解析 ``HH:MM`` 本地运行时刻，缺失或非法时返回默认时刻。

    Args:
        raw: TOML 中 ``[memory].review_time`` 的原始值，预期格式为 ``HH:MM``。
    """

    if not raw:
        return DEFAULT_REVIEW_TIME
    try:
        hh, mm = raw.split(":")
        return time(int(hh), int(mm))
    except (ValueError, AttributeError):
        logger.warning(
            "[memory] invalid review_time %r, using default %s", raw, DEFAULT_REVIEW_TIME
        )
        return DEFAULT_REVIEW_TIME


def _parse_enabled(raw: Any, *, present: bool) -> bool:
    """读取启用开关，只接受 TOML bool，缺失或非法时返回默认值。

    Args:
        raw: ``review_enabled`` 的解析结果。
        present: 配置中是否明确写了 ``review_enabled``；只影响非法值告警。
    """
    if isinstance(raw, bool):
        return raw
    if present:
        logger.warning(
            "[memory] invalid review_enabled %r (expected bool), using default %s",
            raw,
            DEFAULT_REVIEW_ENABLED,
        )
    return DEFAULT_REVIEW_ENABLED


def load_review_config(config_path: Path | None = None) -> ReviewScheduleConfig:
    """从 TOML 的 ``[memory]`` 表读取 Daily review 调度配置。

    配置文件、表或字段缺失，以及文件不可读、TOML 损坏或字段非法时，相应字段
    回退为每日 02:30 和启用状态。

    Args:
        config_path: 要读取的配置文件；为 None 时按项目规则查找配置。
    """
    path = config_path or paths.find_config_path()
    if not path.exists():
        return ReviewScheduleConfig(DEFAULT_REVIEW_TIME, DEFAULT_REVIEW_ENABLED)
    try:
        with path.open("rb") as f:
            data = tomllib.load(f)
    except (tomllib.TOMLDecodeError, OSError):
        logger.warning("[memory] config %s unreadable, using review defaults", path)
        return ReviewScheduleConfig(DEFAULT_REVIEW_TIME, DEFAULT_REVIEW_ENABLED)
    mem = data.get("memory", {}) if isinstance(data, dict) else {}
    review_time = _parse_time(mem.get("review_time"))
    review_enabled = _parse_enabled(
        mem.get("review_enabled"), present="review_enabled" in mem
    )
    return ReviewScheduleConfig(review_time, review_enabled)


def _default_dispatch(event: dict[str, Any]) -> None:
    """首次调用时注册 review job，随后走与 CLI 相同的 hook 链。"""
    global _REVIEW_JOB_REGISTERED
    from trowel_py.memory import hooks
    from trowel_py.memory.review_job import run_daily_review_sync

    with _REG_LOCK:
        if not _REVIEW_JOB_REGISTERED:
            hooks.default.register_write_job(run_daily_review_sync)
            _REVIEW_JOB_REGISTERED = True
    hooks.default.dispatch_write_job(event)


class MemoryReviewScheduler:
    """串行处理即时关闭请求、启动补跑和每日定时任务。"""

    def __init__(
        self,
        config: ReviewScheduleConfig,
        memory_root: Path,
        *,
        dispatch_fn: DispatchFn | None = None,
        now_fn: NowFn | None = None,
        sleep_fn: SleepFn | None = None,
        resource_registry: ResourceRegistry | None = None,
    ) -> None:
        """配置调度时间和派发依赖。

        Args:
            config: review 的启用状态和本地触发时刻。
            memory_root: 调度事件传给 review job 的 Memory 根目录。
            dispatch_fn: 在线程中同步派发 review 事件的函数；为 ``None`` 时使用
                默认 hook registry。
            now_fn: 返回用于计算 review 日期的本地时间；带时区的值按其本地
                时钟字段使用，不转换时区。
            sleep_fn: 每日循环使用的异步等待函数。
            resource_registry: 内部 review LLM 进程使用的应用资源账本。
        """

        self._config = config
        self._memory_root = memory_root
        self._dispatch: DispatchFn = dispatch_fn or _default_dispatch
        self._now: NowFn = now_fn or datetime.now
        self._sleep: SleepFn = sleep_fn or asyncio.sleep
        self._resource_registry = resource_registry
        self._tasks: list[asyncio.Task[None]] = []
        self._immediate_wakeup = asyncio.Event()
        self._dispatch_lock = asyncio.Lock()
        self._active_dispatches: set[asyncio.Task[None]] = set()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._started = False

    @property
    def tasks(self) -> tuple[asyncio.Task[None], ...]:
        """返回当前 catch-up 和每日循环任务的只读快照。"""

        return tuple(self._tasks)

    async def start(self) -> None:
        """启动关闭请求 worker，并按配置启动 catch-up 和每日循环。"""
        if self._started:
            return
        self._started = True
        self._loop = asyncio.get_running_loop()
        logger.info(
            "[memory] review scheduler started (daily at %s, root=%s)",
            self._config.review_time,
            self._memory_root,
        )
        self._tasks.append(
            asyncio.create_task(
                self._immediate_loop(),
                name="memory-review-immediate",
            )
        )
        self._immediate_wakeup.set()
        if self._config.review_enabled:
            self._tasks.append(
                asyncio.create_task(self._catchup(), name="memory-review-catchup")
            )
            self._tasks.append(
                asyncio.create_task(self._daily_loop(), name="memory-review-daily")
            )

    async def stop(self) -> None:
        """停止领取新任务，不等待已经进入线程的 LLM review。"""
        for task in self._tasks:
            task.cancel()
        for task in self._tasks:
            try:
                await task
            except asyncio.CancelledError:
                pass
            except Exception:
                logger.exception(
                    "[memory] scheduler task %s raised on shutdown", task.get_name()
                )
        self._tasks.clear()
        self._loop = None
        self._started = False

    def request_session_review(self, binding: SessionBinding) -> None:
        """先持久登记关闭请求，再唤醒不阻塞关闭操作的后台 worker。

        Args:
            binding: 已关闭 runtime、尚未删除持久 binding 的用户会话。
        """

        enqueue_session_review(self._memory_root, binding, now_fn=self._now)
        loop = self._loop
        if loop is not None and loop.is_running():
            loop.call_soon_threadsafe(self._immediate_wakeup.set)

    async def _immediate_loop(self) -> None:
        """持续处理持久队列；仍有任务时按上限退避重试。"""

        retry_delay = IMMEDIATE_RETRY_MIN_SECONDS
        while True:
            try:
                await self._immediate_wakeup.wait()
            except asyncio.CancelledError:
                logger.info("[memory] immediate review loop cancelled")
                return
            while True:
                self._immediate_wakeup.clear()
                try:
                    requests = await asyncio.to_thread(
                        load_session_review_requests,
                        self._memory_root,
                        eligible_at=self._local_wall_clock_now().isoformat(
                            timespec="microseconds"
                        ),
                    )
                except Exception:
                    logger.exception("[memory] failed to read immediate review queue")
                    requests = None
                if requests is not None:
                    for request in requests:
                        event = {
                            "date": request.requested_at[:10],
                            "root": str(self._memory_root),
                            "review_session_id": request.trowel_session_id,
                        }
                        if self._resource_registry is not None:
                            event["_resource_registry"] = self._resource_registry
                        try:
                            async with self._dispatch_lock:
                                await self._dispatch_in_thread(event)
                        except asyncio.CancelledError:
                            raise
                        except Exception:
                            logger.exception(
                                "[memory] immediate review dispatch failed for %s",
                                request.trowel_session_id,
                            )
                try:
                    remaining = await asyncio.to_thread(
                        load_session_review_requests,
                        self._memory_root,
                    )
                except Exception:
                    logger.exception(
                        "[memory] failed to re-read immediate review queue"
                    )
                    remaining = None
                if remaining == []:
                    retry_delay = IMMEDIATE_RETRY_MIN_SECONDS
                    break
                wait_timeout = self._next_immediate_wait(remaining, retry_delay)
                try:
                    await asyncio.wait_for(
                        self._immediate_wakeup.wait(),
                        timeout=wait_timeout,
                    )
                except asyncio.TimeoutError:
                    pass
                retry_delay = min(
                    retry_delay * 2,
                    IMMEDIATE_RETRY_MAX_SECONDS,
                )

    def _next_immediate_wait(
        self,
        requests: list[ReviewRequest],
        retry_delay: float,
    ) -> float:
        """已到期请求按退避重试，纯未来请求精确等到最近截止时间。"""

        now = self._local_wall_clock_now()
        deadlines = [datetime.fromisoformat(request.not_before) for request in requests]
        if any(deadline <= now for deadline in deadlines):
            return retry_delay
        return max(min((deadline - now).total_seconds() for deadline in deadlines), 0.0)

    async def _catchup(self) -> None:
        """应用启动后立即派发一次昨天的 review。"""

        await self._run_once(label="catchup")

    async def _daily_loop(self) -> None:
        """每天等到配置时刻后派发 review，等待被取消时正常退出。"""
        while True:
            try:
                wait = seconds_until(
                    self._config.review_time,
                    self._local_wall_clock_now(),
                )
                await self._sleep(wait)
            except asyncio.CancelledError:
                logger.info("[memory] daily review loop cancelled")
                return
            await self._run_once(label="daily")

    async def _run_once(self, *, label: str = "run") -> None:
        """在线程中派发一次 review；失败只记录日志，不能拖垮应用。"""
        now = self._local_wall_clock_now()
        cutoff = now.replace(hour=0, minute=0, second=0, microsecond=0)
        event = {
            "date": (cutoff.date() - timedelta(days=1)).isoformat(),
            "eligible_before": cutoff.isoformat(),
            "root": str(self._memory_root),
        }
        if self._resource_registry is not None:
            event["_resource_registry"] = self._resource_registry
        try:
            async with self._dispatch_lock:
                await self._dispatch_in_thread(event)
        except Exception:
            logger.exception("[memory] review dispatch (%s) failed", label)

    async def _dispatch_in_thread(self, event: dict[str, Any]) -> None:
        """派发同步 review，并保留线程任务供关闭流程有界等待。"""

        task = asyncio.create_task(asyncio.to_thread(self._dispatch, event))
        self._active_dispatches.add(task)
        task.add_done_callback(self._finish_dispatch)
        await asyncio.shield(task)

    def _finish_dispatch(self, task: asyncio.Task[None]) -> None:
        """移除已结束线程任务，并领取异常避免孤儿 task 告警。"""

        self._active_dispatches.discard(task)
        if not task.cancelled():
            task.exception()

    def _local_wall_clock_now(self) -> datetime:
        """返回与 SQLite 截止时间文本一致的本地无时区当前时间。"""

        now = self._now()
        return now.replace(tzinfo=None) if now.tzinfo is not None else now
