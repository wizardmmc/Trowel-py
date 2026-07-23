"""在应用生命周期内调度 daily memory review。"""
from __future__ import annotations

import asyncio
import logging
import threading
import tomllib
from dataclasses import dataclass
from datetime import date, datetime, time
from pathlib import Path
from typing import Any, Awaitable, Callable

from trowel_py.memory import paths
from trowel_py.memory.scheduling import seconds_until

logger = logging.getLogger("trowel_py.memory.review_scheduler")

DEFAULT_REVIEW_TIME: time = time(2, 30)
DEFAULT_REVIEW_ENABLED: bool = True
DispatchFn = Callable[[dict[str, Any]], None]
NowFn = Callable[[], datetime]
SleepFn = Callable[[float], Awaitable[None]]

# dispatch 在线程中运行，注册检查必须加锁，不能依赖进程级 bool 的原子性。
_REG_LOCK = threading.Lock()
_REVIEW_JOB_REGISTERED = False


@dataclass(frozen=True)
class ReviewScheduleConfig:
    review_time: time
    review_enabled: bool


def _parse_time(raw: str | None) -> time:
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
    """只接受 TOML bool，避免字符串 ``"false"`` 被 Python 当成真值。"""
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
    """读取 ``[memory]`` 调度配置；缺失、损坏或非法值均回退默认值。"""
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
    """在应用进程内维护启动补跑和每日定时任务。"""

    def __init__(
        self,
        config: ReviewScheduleConfig,
        memory_root: Path,
        *,
        dispatch_fn: DispatchFn | None = None,
        now_fn: NowFn | None = None,
        sleep_fn: SleepFn | None = None,
    ) -> None:
        self._config = config
        self._memory_root = memory_root
        self._dispatch: DispatchFn = dispatch_fn or _default_dispatch
        self._now: NowFn = now_fn or datetime.now
        self._sleep: SleepFn = sleep_fn or asyncio.sleep
        self._tasks: list[asyncio.Task[None]] = []
        self._started = False

    @property
    def tasks(self) -> tuple[asyncio.Task[None], ...]:
        return tuple(self._tasks)

    async def start(self) -> None:
        """启动补跑和每日循环；禁用或已启动时保持幂等。"""
        if self._started or not self._config.review_enabled:
            return
        self._started = True
        logger.info(
            "[memory] review scheduler started (daily at %s, root=%s)",
            self._config.review_time,
            self._memory_root,
        )
        self._tasks.append(asyncio.create_task(self._catchup(), name="memory-review-catchup"))
        self._tasks.append(asyncio.create_task(self._daily_loop(), name="memory-review-daily"))

    async def stop(self) -> None:
        """取消调度 task；线程中已开始的 review 不会被强制终止。"""
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
        self._started = False

    async def _catchup(self) -> None:
        await self._run_once(label="catchup")

    async def _daily_loop(self) -> None:
        """等待目标时刻并循环派发；sleep 期间取消时正常退出。"""
        while True:
            try:
                wait = seconds_until(self._config.review_time, self._now())
                await self._sleep(wait)
            except asyncio.CancelledError:
                logger.info("[memory] daily review loop cancelled")
                return
            await self._run_once(label="daily")

    async def _run_once(self, *, label: str = "run") -> None:
        """在线程中派发一次 review；失败只记录日志，不能拖垮应用。"""
        event = {"date": date.today().isoformat(), "root": str(self._memory_root)}
        try:
            await asyncio.to_thread(self._dispatch, event)
        except Exception:
            logger.exception("[memory] review dispatch (%s) failed", label)
