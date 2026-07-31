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

logger = logging.getLogger(__name__)

# 默认比 daily review 晚 20 分钟，降低两个 CC 任务争抢额度的概率。
DEFAULT_DISTILL_TIME: time = time(2, 50)
DEFAULT_DISTILL_ENABLED: bool = True

DispatchFn = Callable[[dict[str, Any]], None]
NowFn = Callable[[], datetime]
SleepFn = Callable[[float], Awaitable[None]]


@dataclass(frozen=True)
class DistillScheduleConfig:
    """记录 Profile 提炼的启用状态和每日本地触发时刻。

    Attributes:
        distill_time: 每天按本地时钟触发提炼的时刻。
        distill_enabled: 是否在应用启动时启用补跑和每日循环。
    """

    distill_time: time
    distill_enabled: bool


def _parse_time(raw: str | None) -> time:
    """解析以冒号分隔的本地小时和分钟，缺失或非法时返回默认时刻。

    Args:
        raw: TOML 中 ``[memory].distill_time`` 的原始值，例如 ``"02:30"``。

    Returns:
        解析后的时刻；缺失、类型错误、格式错误或越界时为 02:30。
    """

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
    """读取启用开关，只接受 TOML bool，缺失或非法时返回默认值。

    Args:
        raw: ``distill_enabled`` 的解析结果。
        present: 配置中是否明确写了该字段；只影响非法值告警。

    Returns:
        配置中的布尔值，或默认启用状态。
    """
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
    """从 TOML 的 ``[memory]`` 表读取 Profile 提炼调度配置。

    配置文件、表或字段缺失，以及文件不可读、TOML 损坏或字段值非法时，相应
    字段回退为每日 02:30 和启用状态。合法 TOML 若把 ``memory`` 写成非表值，
    后续属性错误会原样传播。

    Args:
        config_path: 要读取的配置文件；为 ``None`` 时按项目规则查找配置。

    Returns:
        解析结果及必要的字段级默认值。

    Raises:
        AttributeError: ``memory`` 是合法 TOML 值但不是表。
    """
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
    from trowel_py.profile.distill.batch import run_daily_distill_sync

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
        """配置调度时间和运行时参数。

        Args:
            config: 提炼的启用状态和本地触发时刻。
            memory_root: 调度事件传给提炼任务的 Memory 根目录。
            proxy_base_url: 调度事件携带的代理地址；本类不校验是否为空。
            settings_path: 调度事件携带的 provider settings 路径。
            dispatch_fn: 在线程中同步处理事件的函数；为 ``None`` 时直接运行
                默认提炼任务。
            now_fn: 返回调度所用本地时间；带时区值按显示的时钟字段使用，
                不转换时区。
            sleep_fn: 每日循环使用的异步等待函数。
        """

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
        """返回当前补跑和每日循环任务的元组快照。"""

        return tuple(self._tasks)

    async def start(self) -> None:
        """启动一次立即补跑和每日循环；禁用或已启动时不创建任务。"""
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
        """应用启动后立即尝试派发一次截至今天零点的补跑。"""

        await self._run_once(label="catchup")

    async def _daily_loop(self) -> None:
        """每天等到配置时刻后派发提炼，等待被取消时正常退出。"""
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
