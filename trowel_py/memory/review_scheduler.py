"""in-app daily memory review scheduler (slice-046).

Replaces the launchd path from slice-040-b (``schedule.py``): instead of a
system-level plist the trowel app process schedules the daily review itself.

Two triggers, both going through the SAME dispatch chain as
``trowel memory review`` (``hooks.default.dispatch_write_job`` ->
``run_daily_review_sync``):

- **startup catchup**: fire once on ``start()`` so a missed run (app closed /
  Mac asleep) is made up the moment the user opens trowel.
- **daily fixed-time**: every day at ``review_time`` (default 02:30).

Idempotency / safety are inherited from ``review_job``, NOT re-implemented:

- flock (``meta/.review.lock``) — catchup and fixed-time never run two reviews
  at once (040-b C-3).
- watermark (``find_incremental``) — already-extracted sessions are skipped, so
  catchup never re-distills (040-b C-4/C-6/C-7).
- CCHost spawns cc as a subprocess — no nested ``claude -p`` (040-b C-3 / #46416).

The dispatch runs in a worker thread (``asyncio.to_thread``) so a long
distillation never blocks the uvicorn event loop (C-1). Any exception from
dispatch is logged and swallowed (C-5) — a review failure must never take the
app down.
"""
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

logger = logging.getLogger(__name__)

#: default daily review time (02:30 — late night, low GLM load).
DEFAULT_REVIEW_TIME: time = time(2, 30)
#: default enabled state (scheduling on unless config opts out).
DEFAULT_REVIEW_ENABLED: bool = True
#: seconds in a day — the roll-to-tomorrow delta in ``seconds_until``.
_SECONDS_PER_DAY: int = 24 * 3600

#: dispatch payload type: the event handed to ``dispatch_write_job``.
DispatchFn = Callable[[dict[str, Any]], None]
#: now injector type (tests pass a fake clock).
NowFn = Callable[[], datetime]
#: sleep injector type (asyncio.sleep or a test fake returning None).
SleepFn = Callable[[float], Awaitable[None]]

#: guards the one-time register of ``run_daily_review_sync``. The dispatch
#: runs in a worker thread (``asyncio.to_thread``), so a plain bool would race
#: on the check-then-register and could double-register.
_REG_LOCK = threading.Lock()
#: process-wide guard so ``run_daily_review_sync`` is registered once.
_REVIEW_JOB_REGISTERED = False


@dataclass(frozen=True)
class ReviewScheduleConfig:
    """Resolved scheduling config for the daily memory review.

    Attributes:
        review_time: the wall-clock time the daily loop fires at.
        review_enabled: when False, ``start()`` schedules nothing (C-6).
    """

    review_time: time
    review_enabled: bool


def _parse_time(raw: str | None) -> time:
    """Parse an ``HH:MM`` string into a ``time``.

    Falls back to the default on any parse failure (C-7 — a bad value must
    never stop the app), logging a warning.
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
    """Coerce a ``review_enabled`` config value to bool, strictly.

    A non-bool (e.g. the TOML string ``"false"``) would mis-enable the
    scheduler if naively ``bool()``'d (``bool("false")`` is True), so fall back
    to the default + warn (C-7 — mirrors ``_parse_time``'s strictness).
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
    """Load the review schedule from the ``[memory]`` section of config.toml.

    Args:
        config_path: explicit config location for testability; defaults to the
            standard lookup (cwd then ``~/.trowel`` — mirrors ``paths``).

    Returns:
        A ``ReviewScheduleConfig``. Missing file / section / keys fall back to
        defaults (02:30, enabled). Invalid ``review_time`` / ``review_enabled``
        fall back with a warning (C-7) — never raises.
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


def seconds_until(target: time, now: datetime) -> float:
    """Seconds from ``now`` to the next occurrence of ``target`` HH:MM.

    If ``target`` is still ahead today, returns the gap; if it has passed (or
    is exactly now), rolls to tomorrow (+``_SECONDS_PER_DAY``). Pure — no I/O,
    no wall clock.

    Args:
        target: the daily target time.
        now: the reference moment.

    Returns:
        Seconds (always > 0) until the next fire.
    """
    today_target = now.replace(hour=target.hour, minute=target.minute, second=0, microsecond=0)
    delta = (today_target - now).total_seconds()
    if delta <= 0:
        delta += _SECONDS_PER_DAY
    return delta


def _default_dispatch(event: dict[str, Any]) -> None:
    """Dispatch the review write-job over the process-wide hook registry.

    Idempotently registers ``run_daily_review_sync`` on first call under a
    lock: the dispatch runs in a worker thread (``asyncio.to_thread``), and the
    lock prevents two threads racing the check-then-register and
    double-registering (which — since ``register_write_job`` is now deduped —
    is belt-and-suspenders, but keeps the register path single-shot). Then
    delegates to ``hooks.default.dispatch_write_job`` — the same chain
    ``trowel memory review`` walks.
    """
    global _REVIEW_JOB_REGISTERED
    from trowel_py.memory import hooks
    from trowel_py.memory.review_job import run_daily_review_sync

    with _REG_LOCK:
        if not _REVIEW_JOB_REGISTERED:
            hooks.default.register_write_job(run_daily_review_sync)
            _REVIEW_JOB_REGISTERED = True
    hooks.default.dispatch_write_job(event)


class MemoryReviewScheduler:
    """Schedules the daily memory review inside the app process.

    Construct on app startup, ``await start()`` to launch the catchup + daily
    tasks, ``await stop()`` on shutdown to cancel them. The dispatch runs in a
    worker thread so a long distillation never blocks the event loop.
    """

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
        """Snapshot of live scheduler tasks (catchup + daily loop). Read-only."""
        return tuple(self._tasks)

    async def start(self) -> None:
        """Launch catchup + daily-loop tasks (C-1: returns immediately).

        No-op if disabled (C-6) or this instance already started. On uvicorn
        ``--reload`` a fresh instance is built and runs catchup again — that is
        safe because catchup is idempotent via the review_job watermark (C-3),
        and flock (C-2) keeps two instances from running a review at once.
        """
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
        """Cancel catchup + daily-loop tasks.

        A review already running is not force-killed — flock + watermark make
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
                    "[memory] scheduler task %s raised on shutdown", task.get_name()
                )
        self._tasks.clear()
        self._started = False

    async def _catchup(self) -> None:
        """Fire one review immediately on start (the missed-run makeup)."""
        await self._run_once(label="catchup")

    async def _daily_loop(self) -> None:
        """Sleep until ``review_time``, fire, repeat. Stops cleanly on cancel.

        Only the sleep is guarded against ``CancelledError``: a cancel during
        ``_run_once`` (inside ``asyncio.to_thread``) propagates after the
        worker thread finishes and is caught by ``stop()``'s await, so the loop
        only needs to exit cleanly on a sleep-time cancel.
        """
        while True:
            try:
                wait = seconds_until(self._config.review_time, self._now())
                await self._sleep(wait)
            except asyncio.CancelledError:
                logger.info("[memory] daily review loop cancelled")
                return
            await self._run_once(label="daily")

    async def _run_once(self, *, label: str = "run") -> None:
        """Dispatch one review in a worker thread; swallow any failure (C-5).

        ``date.today()`` is captured at dispatch time (not injected) — that's
        fine: review_job processes sessions by watermark across dates, so the
        payload date only tags the run (不依赖单天语义).

        Args:
            label: logged tag identifying the trigger (catchup / daily).
        """
        event = {"date": date.today().isoformat(), "root": str(self._memory_root)}
        try:
            await asyncio.to_thread(self._dispatch, event)
        except Exception:
            logger.exception("[memory] review dispatch (%s) failed", label)
