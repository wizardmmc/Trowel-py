"""in-app daily profile distill scheduler (slice-050).

The sister of review_scheduler: schedules ``profile_distill_job`` inside the
app process. Two triggers through the same dispatch
(``run_daily_distill_sync`` → ``run_daily_distill``):

- **startup catchup**: fire once on ``start()`` so a missed run is made up.
- **daily fixed-time**: every day at ``distill_time`` (default 02:50, off-phase
  from review's 02:30 so the two cc-spawning jobs don't collide).

Idempotency / safety are inherited from ``profile_distill_job`` (flock +
watermark), NOT re-implemented. The dispatch runs in a worker thread
(``asyncio.to_thread``) so a long distill never blocks the uvicorn event loop.
Any exception is logged + swallowed — a distill failure must never take the app
down (C-6).

Diverges from review_scheduler: it carries ``proxy_base_url`` (C-4 — distill
goes through the trowel proxy) into the dispatch event, so ``run_daily_distill``
can hand it to the CCHost. ``seconds_until`` is reused from review_scheduler
(it's pure).
"""
from __future__ import annotations

import asyncio
import logging
import tomllib
from dataclasses import dataclass
from datetime import date, datetime, time
from pathlib import Path
from typing import Any, Awaitable, Callable

from trowel_py.memory import paths
from trowel_py.memory.review_scheduler import seconds_until

logger = logging.getLogger(__name__)

#: default daily distill time (02:50 — 20 min after review's 02:30 so the two
#: cc-spawning jobs don't overlap on the GLM rate limit).
DEFAULT_DISTILL_TIME: time = time(2, 50)
#: default enabled state (scheduling on unless config opts out).
DEFAULT_DISTILL_ENABLED: bool = True

#: dispatch payload type + clock/sleep injectors (mirrors review_scheduler).
DispatchFn = Callable[[dict[str, Any]], None]
NowFn = Callable[[], datetime]
SleepFn = Callable[[float], Awaitable[None]]


@dataclass(frozen=True)
class DistillScheduleConfig:
    """Resolved scheduling config for the daily profile distill.

    Attributes:
        distill_time: the wall-clock time the daily loop fires at.
        distill_enabled: when False, ``start()`` schedules nothing.
    """

    distill_time: time
    distill_enabled: bool


def _parse_time(raw: str | None) -> time:
    """Parse an ``HH:MM`` string; fall back to the default on failure (never raise)."""
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
    """Coerce distill_enabled strictly (bool("false") is True — guard against it)."""
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
    """Load the distill schedule from the ``[memory]`` section of config.toml.

    Args:
        config_path: explicit config location for testability; defaults to the
            standard lookup (cwd then ``~/.trowel`` — mirrors ``paths``).

    Returns:
        A ``DistillScheduleConfig``. Missing file / section / keys fall back to
        defaults (02:50, enabled). Invalid values fall back with a warning,
        never raises.
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
    """Dispatch the distill job directly.

    Unlike review_scheduler's hooks-registry dispatch, distill has no CLI twin,
    so it calls ``run_daily_distill_sync`` straight (the flock + watermark
    idempotency live inside the job, not the dispatch chain).
    """
    from trowel_py.memory.profile_distill_job import run_daily_distill_sync

    run_daily_distill_sync(event)


class ProfileDistillScheduler:
    """Schedules the daily profile distill inside the app process.

    Construct on app startup with ``proxy_base_url``, ``await start()`` to
    launch the catchup + daily tasks, ``await stop()`` on shutdown to cancel.
    The dispatch runs in a worker thread so a long distill never blocks the
    event loop.
    """

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
        """Snapshot of live scheduler tasks (catchup + daily loop). Read-only."""
        return tuple(self._tasks)

    async def start(self) -> None:
        """Launch catchup + daily-loop tasks (returns immediately).

        No-op if disabled or already started. Safe under uvicorn ``--reload``:
        a fresh instance runs catchup again, but flock + watermark keep two
        instances from double-distilling.
        """
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
        """Cancel catchup + daily-loop tasks.

        A distill already running is not force-killed — flock + watermark make
        the next start resume safely.
        """
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
        """Fire one distill immediately on start (the missed-run makeup)."""
        await self._run_once(label="catchup")

    async def _daily_loop(self) -> None:
        """Sleep until ``distill_time``, fire, repeat. Stops cleanly on cancel."""
        while True:
            try:
                wait = seconds_until(self._config.distill_time, self._now())
                await self._sleep(wait)
            except asyncio.CancelledError:
                logger.info("[memory] daily distill loop cancelled")
                return
            await self._run_once(label="daily")

    async def _run_once(self, *, label: str = "run") -> None:
        """Dispatch one distill in a worker thread; swallow any failure (C-6).

        The event carries ``proxy_base_url`` so the job can hand it to the CCHost.
        """
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
