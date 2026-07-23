"""在应用生命周期内调度每日 profile distill。"""
from __future__ import annotations

import asyncio
import logging
import tomllib
from dataclasses import dataclass
from datetime import date, datetime, time
from pathlib import Path
from typing import Any, Awaitable, Callable

from trowel_py.memory import paths
from trowel_py.memory.scheduling import seconds_until

logger = logging.getLogger("trowel_py.memory.profile_distill_scheduler")

# 默认比 daily review 晚 20 分钟，降低两个 CC 任务争抢额度的概率。
DEFAULT_DISTILL_TIME: time = time(2, 50)
DEFAULT_DISTILL_ENABLED: bool = True

DispatchFn = Callable[[dict[str, Any]], None]
NowFn = Callable[[], datetime]
SleepFn = Callable[[float], Awaitable[None]]


@dataclass(frozen=True)
class DistillScheduleConfig:
    distill_time: time
    distill_enabled: bool


def _parse_time(raw: str | None) -> time:
    if not raw:
        return DEFAULT_DISTILL_TIME
    try:
        hh, mm = raw.split(":")
        return time(int(hh), int(mm))
    except (ValueError, AttributeError):
        logger.warning(
            "[memory] invalid distill_time %r, using default %s",
            raw,
            DEFAULT_DISTILL_TIME,
        )
        return DEFAULT_DISTILL_TIME


def _parse_enabled(raw: Any, *, present: bool) -> bool:
    """只接受 TOML bool，避免字符串 ``"false"`` 被 Python 当成真值。"""
    if isinstance(raw, bool):
        return raw
    if present:
        logger.warning(
            "[memory] invalid distill_enabled %r (expected bool), using default %s",
            raw,
            DEFAULT_DISTILL_ENABLED,
        )
    return DEFAULT_DISTILL_ENABLED


def load_distill_config(config_path: Path | None = None) -> DistillScheduleConfig:
    """读取 ``[memory]`` 调度配置；缺失、损坏或非法值均回退默认值。"""
    path = config_path or paths.find_config_path()
    if not path.exists():
        return DistillScheduleConfig(DEFAULT_DISTILL_TIME, DEFAULT_DISTILL_ENABLED)
    try:
        with path.open("rb") as f:
            data = tomllib.load(f)
    except (tomllib.TOMLDecodeError, OSError):
        logger.warning("[memory] config %s unreadable, using distill defaults", path)
        return DistillScheduleConfig(DEFAULT_DISTILL_TIME, DEFAULT_DISTILL_ENABLED)
    mem = data.get("memory", {}) if isinstance(data, dict) else {}
    distill_time = _parse_time(mem.get("distill_time"))
    distill_enabled = _parse_enabled(
        mem.get("distill_enabled"), present="distill_enabled" in mem
    )
    return DistillScheduleConfig(distill_time, distill_enabled)


def _default_dispatch(event: dict[str, Any]) -> None:
    """直接调用 distill job；并发锁和水位幂等由 job 自身负责。"""
    from trowel_py.memory.profile_distill_job import run_daily_distill_sync

    run_daily_distill_sync(event)


class ProfileDistillScheduler:
    """在应用进程内维护启动补跑和每日定时提炼。"""

    def __init__(
        self,
        config: DistillScheduleConfig,
        memory_root: Path,
        proxy_base_url: str,
        settings_path: Path | str | None = None,
        *,
        dispatch_fn: DispatchFn | None = None,
        now_fn: NowFn | None = None,
        sleep_fn: SleepFn | None = None,
    ) -> None:
        self._config = config
        self._memory_root = memory_root
        self._proxy_base_url = proxy_base_url
        self._settings_path = settings_path
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
        if self._started or not self._config.distill_enabled:
            return
        self._started = True
        logger.info(
            "[memory] profile distill scheduler started (daily at %s, root=%s)",
            self._config.distill_time,
            self._memory_root,
        )
        self._tasks.append(
            asyncio.create_task(self._catchup(), name="profile-distill-catchup")
        )
        self._tasks.append(
            asyncio.create_task(self._daily_loop(), name="profile-distill-daily")
        )

    async def stop(self) -> None:
        """取消调度 task；线程中已开始的 distill 不会被强制终止。"""
        for task in self._tasks:
            task.cancel()
        for task in self._tasks:
            try:
                await task
            except asyncio.CancelledError:
                pass
            except Exception:
                logger.exception(
                    "[memory] distill scheduler task %s raised on shutdown",
                    task.get_name(),
                )
        self._tasks.clear()
        self._started = False

    async def _catchup(self) -> None:
        await self._run_once(label="catchup")

    async def _daily_loop(self) -> None:
        """等待目标时刻并循环派发；sleep 期间取消时正常退出。"""
        while True:
            try:
                wait = seconds_until(self._config.distill_time, self._now())
                await self._sleep(wait)
            except asyncio.CancelledError:
                logger.info("[memory] daily distill loop cancelled")
                return
            await self._run_once(label="daily")

    async def _run_once(self, *, label: str = "run") -> None:
        """在线程中派发一次提炼；代理信息随事件传递，失败不影响应用。"""
        event = {
            "date": date.today().isoformat(),
            "root": str(self._memory_root),
            "proxy_base_url": self._proxy_base_url,
            "settings_path": str(self._settings_path) if self._settings_path else None,
        }
        try:
            await asyncio.to_thread(self._dispatch, event)
        except Exception:
            logger.exception("[memory] profile distill dispatch (%s) failed", label)
