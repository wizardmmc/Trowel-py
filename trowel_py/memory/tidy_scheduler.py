"""in-app weekly/monthly tidy scheduler (slice-052).

The sister of review_scheduler / profile_distill_scheduler, but tidy does NOT
spawn cc — Python calls the provider directly (recompute → compress → plan →
apply, C-4). So this scheduler is simpler: no CCHost, no hooks registry, just
two loops calling the tidy functions in a worker thread.

- **weekly_loop**: every Monday 03:30 → run_weekly_tidy(root, LAST iso_week)
- **monthly_loop**: every 1st 04:00 → run_monthly_tidy(root, LAST month)

Both fire on already-COMPLETED intervals (C-3 — never tidy the in-progress
week/month). No startup catchup: tidy is re-runnable (a missed week's notes
fold into the next run's scope), and unlike review/distill there's no
watermark to catch up against. Any tidy failure is logged + swallowed (C-5) —
never takes the app down. now / sleep / tidy-fn are injectable so tests need
no wall-clock sleep and no real provider (#46416).
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, time, timedelta
from pathlib import Path
from typing import Any, Awaitable, Callable

from trowel_py.llm.client import LLMProvider

logger = logging.getLogger(__name__)

#: weekly tidy fires Monday 03:30 (after review 02:30 / distill 02:50).
DEFAULT_WEEKLY_TIME: time = time(3, 30)
#: monthly tidy fires the 1st at 04:00 (after weekly's 03:30).
DEFAULT_MONTHLY_TIME: time = time(4, 0)

#: date.weekday() code for Monday.
_MONDAY: int = 0
#: day-of-month the monthly loop fires on.
_FIRST: int = 1

#: clock / sleep injectors (mirror review_scheduler).
NowFn = Callable[[], datetime]
SleepFn = Callable[[float], Awaitable[None]]
#: a tidy step: takes the period key (iso_week / month), does all the work.
TidyFn = Callable[[str], Any]
#: produces a fresh provider per tidy run (C-4 — direct provider call, no cc).
ProviderFactory = Callable[[], LLMProvider]


def last_iso_week(now: datetime) -> str:
    """The ISO week of the most-recent COMPLETED week at ``now`` (C-3).

    ``now - 7d`` always lands in the previous ISO week no matter today's
    weekday, so this is safe from a catchup/missed run too (not just Monday
    03:30). Handles 跨年 (e.g. 2027-01-04 → 2026-W53). Returns ``"YYYY-Www"``.
    """
    prev = (now - timedelta(days=7)).date()
    y, w, _ = prev.isocalendar()
    return f"{y:04d}-W{w:02d}"


def last_month(now: datetime) -> str:
    """The most-recent COMPLETED month at ``now`` (C-3) as ``"YYYY-MM"``.

    The day before this month's 1st is last month's last day — robust on any
    trigger day, not just the 1st.
    """
    first = now.date().replace(day=1)
    return (first - timedelta(days=1)).strftime("%Y-%m")


def seconds_until_next_weekday(
    now: datetime, weekday: int, target: time
) -> float:
    """Seconds from ``now`` to the next ``weekday`` at ``target`` HH:MM.

    If that weekday is still ahead this week (incl. today with ``target``
    ahead), it's this week; otherwise rolls +7 days. Pure — no I/O.
    """
    days_ahead = (weekday - now.weekday()) % 7
    candidate = (now + timedelta(days=days_ahead)).replace(
        hour=target.hour, minute=target.minute, second=0, microsecond=0
    )
    if candidate <= now:
        candidate += timedelta(days=7)
    return (candidate - now).total_seconds()


def seconds_until_next_monthday(
    now: datetime, day: int, target: time
) -> float:
    """Seconds from ``now`` to the next month-day ``day`` at ``target`` HH:MM.

    Walks forward month by month until it finds a month whose ``day`` at
    ``target`` is strictly after ``now`` (skips months that lack ``day``, e.g.
    day 31 in Feb). Pure. Bounded at 24 months — a valid target always exists
    well before that (day 1 always exists, so the loop always returns).
    """
    y, m = now.year, now.month
    for _ in range(24):
        try:
            candidate = datetime(y, m, day, target.hour, target.minute, 0, 0)
        except ValueError:  # that month has no such day — try next month
            pass
        else:
            if candidate > now:
                return (candidate - now).total_seconds()
        m += 1
        if m > 12:
            m, y = 1, y + 1
    # unreachable for day=1 (every month has a 1st); defensive fallback.
    return float(24 * 3600)


class TidyScheduler:
    """Schedules weekly + monthly tidy inside the app process.

    Construct on app startup with a ``provider_factory`` (each tidy run gets a
    fresh provider — C-4). ``await start()`` launches the two loops;
    ``await stop()`` cancels them. Each tidy runs in a worker thread
    (``asyncio.to_thread``) so a long tidy never blocks the event loop. Any
    failure is swallowed (C-5).
    """

    def __init__(
        self,
        memory_root: Path,
        provider_factory: ProviderFactory,
        *,
        weekly_time: time = DEFAULT_WEEKLY_TIME,
        monthly_time: time = DEFAULT_MONTHLY_TIME,
        now_fn: NowFn | None = None,
        sleep_fn: SleepFn | None = None,
        weekly_fn: TidyFn | None = None,
        monthly_fn: TidyFn | None = None,
    ) -> None:
        self._memory_root = memory_root
        self._provider_factory = provider_factory
        self._weekly_time = weekly_time
        self._monthly_time = monthly_time
        self._now: NowFn = now_fn or datetime.now
        self._sleep: SleepFn = sleep_fn or asyncio.sleep
        self._weekly_fn: TidyFn = weekly_fn or self._default_weekly
        self._monthly_fn: TidyFn = monthly_fn or self._default_monthly
        self._tasks: list[asyncio.Task[None]] = []
        self._started = False

    def _default_weekly(self, iso_week: str) -> None:
        """Production weekly step: run_weekly_tidy with a fresh provider (C-4)."""
        from trowel_py.memory.tidy import run_weekly_tidy

        run_weekly_tidy(self._memory_root, iso_week, self._provider_factory())

    def _default_monthly(self, month: str) -> None:
        """Production monthly step: run_monthly_tidy with a fresh provider (C-4)."""
        from trowel_py.memory.tidy import run_monthly_tidy

        run_monthly_tidy(self._memory_root, month, self._provider_factory())

    @property
    def tasks(self) -> tuple[asyncio.Task[None], ...]:
        """Snapshot of live scheduler tasks (weekly + monthly loop). Read-only."""
        return tuple(self._tasks)

    async def start(self) -> None:
        """Launch weekly + monthly loops (returns immediately).

        No-op if already started. Unlike review/distill there's no catchup —
        tidy is re-runnable and has no watermark to catch up against.
        """
        if self._started:
            return
        self._started = True
        logger.info(
            "[memory] tidy scheduler started (weekly Mon %s, monthly 1st %s, root=%s)",
            self._weekly_time,
            self._monthly_time,
            self._memory_root,
        )
        self._tasks.append(
            asyncio.create_task(self._weekly_loop(), name="tidy-weekly")
        )
        self._tasks.append(
            asyncio.create_task(self._monthly_loop(), name="tidy-monthly")
        )

    async def stop(self) -> None:
        """Cancel both loops.

        A tidy already running in a worker thread is not force-killed — tidy
        is re-runnable, so the next start resumes safely.
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
                    "[memory] tidy scheduler task %s raised on shutdown",
                    task.get_name(),
                )
        self._tasks.clear()
        self._started = False

    async def _weekly_loop(self) -> None:
        """Sleep until next Monday weekly_time, fire tidy on last ISO week."""
        while True:
            try:
                now = self._now()
                wait = seconds_until_next_weekday(now, _MONDAY, self._weekly_time)
                await self._sleep(wait)
            except asyncio.CancelledError:
                logger.info("[memory] weekly tidy loop cancelled")
                return
            # fire moment = now + wait (the projected trigger; ±seconds don't
            # change the iso_week, so a single now() read is enough — and it
            # keeps tests' now_fn a simple fixed value).
            await self._run_tidy(
                self._weekly_fn,
                last_iso_week(now + timedelta(seconds=wait)),
                "weekly",
            )

    async def _monthly_loop(self) -> None:
        """Sleep until next 1st monthly_time, fire tidy on last month."""
        while True:
            try:
                now = self._now()
                wait = seconds_until_next_monthday(now, _FIRST, self._monthly_time)
                await self._sleep(wait)
            except asyncio.CancelledError:
                logger.info("[memory] monthly tidy loop cancelled")
                return
            await self._run_tidy(
                self._monthly_fn,
                last_month(now + timedelta(seconds=wait)),
                "monthly",
            )

    async def _run_tidy(self, fn: TidyFn, period: str, label: str) -> None:
        """Run one tidy in a worker thread; swallow any failure (C-5)."""
        try:
            await asyncio.to_thread(fn, period)
            logger.info("[memory] %s tidy (%s) done", label, period)
        except Exception:
            logger.exception("[memory] %s tidy (%s) failed", label, period)
