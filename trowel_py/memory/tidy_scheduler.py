"""in-app weekly/monthly tidy scheduler with startup catchup.

slice-052 added the two fire-once loops. slice-063 adds a persistent success
watermark + startup catchup so a missed Monday / 1st is caught up on the next
launch instead of silently dropped (tidy filters by exact ISO week / month,
so the old "the next run absorbs it" assumption was wrong).

- **weekly_loop**: every Monday 03:30 → catch up pending completed ISO weeks.
- **monthly_loop**: every 1st 04:00 → catch up pending completed months, but
  only while weekly isn't behind (C-5 — the monthly report consumes weeklies).
- **startup catchup**: on ``start()`` run weekly then monthly, oldest-first.

Both fire on already-COMPLETED intervals (C-2 — never the in-progress
week/month). Each completed period advances a persistent watermark
(``meta/tidy-state.json``) only on full success; a failure stops that scope so
the next run retries from the failed period (C-3/C-4). catchup runs in worker
threads and never blocks the event loop (C-7); any failure is logged and
swallowed (C-8) — it never takes the app down. ``now`` / ``sleep`` / tidy fns
are injectable so tests need no wall-clock sleep and no real provider (#46416).
"""
from __future__ import annotations

import asyncio
import logging
import threading
from datetime import datetime, time, timedelta
from pathlib import Path
from typing import Any, Awaitable, Callable, Literal

from trowel_py.llm.client import LLMProvider
from trowel_py.memory.tidy_state import (
    MAX_PENDING_MONTHS,
    MAX_PENDING_WEEKS,
    advance_watermark,
    enumerate_pending_months,
    enumerate_pending_weeks,
    last_iso_week,
    last_month,
    load_state,
    next_iso_week,
    next_month,
    save_state,
)

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
#: a tidy step: takes the period key (iso_week / month), returns a report dict.
TidyFn = Callable[[str], Any]
#: produces a fresh provider per tidy run (C-4 — direct provider call, no cc).
ProviderFactory = Callable[[], LLMProvider]
Scope = Literal["weekly", "monthly"]


def _extract_failure(report: Any) -> str | None:
    """Return a human-readable failure reason, or None if ``report`` is a success.

    Single source of truth for the failure-signal check, shared by
    :func:`tidy_succeeded` and the catchup logging.
    """
    if not isinstance(report, dict):
        return f"non-dict report: {type(report).__name__}"
    if report.get("skipped"):
        return f"skipped: {report['skipped']}"
    tidy = report.get("tidy")
    if isinstance(tidy, dict) and tidy.get("error"):
        return f"error: {tidy['error']}"
    return None


def tidy_succeeded(report: Any) -> bool:
    """C-3 success gate: True iff the report carries no failure signal.

    Recognized failure signals: ``skipped`` (lock contention with another tidy)
    and ``tidy.error`` (apply stale/invalid). A clean no-op (``operations ==
    0``) is a success. provider/compress/recompute failures that *raise* escape
    the tidy fn and are caught by the caller's try/except — they never reach
    this function as a dict, so they can't be misread as success here.
    """
    return _extract_failure(report) is None


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
    fresh provider — C-4). ``await start()`` runs the startup catchup then
    launches the two loops; ``await stop()`` cancels them. Each tidy runs in a
    worker thread (``asyncio.to_thread``) so a long tidy never blocks the
    event loop. Any failure is swallowed (C-8). A per-process lock serializes
    catchup runs so the startup catchup and a scheduled wake can't double-fire
    the same scope (the on-disk ``.tidy.lock`` still guards cross-process).
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
        self._stopping = False
        # serializes in-process catchup runs (startup vs scheduled wake)
        self._catchup_lock = threading.Lock()

    def _default_weekly(self, iso_week: str) -> Any:
        """Production weekly step: run_weekly_tidy with a fresh provider (C-4)."""
        from trowel_py.memory.tidy import run_weekly_tidy

        return run_weekly_tidy(self._memory_root, iso_week, self._provider_factory())

    def _default_monthly(self, month: str) -> Any:
        """Production monthly step: run_monthly_tidy with a fresh provider (C-4)."""
        from trowel_py.memory.tidy import run_monthly_tidy

        return run_monthly_tidy(self._memory_root, month, self._provider_factory())

    @property
    def tasks(self) -> tuple[asyncio.Task[None], ...]:
        """Snapshot of live scheduler tasks (startup catchup + loops)."""
        return tuple(self._tasks)

    async def start(self) -> None:
        """Run startup catchup then launch the weekly + monthly loops.

        No-op if already started. The startup catchup runs in a worker thread
        so it never blocks app readiness (C-7); its failure is swallowed (C-8).
        """
        if self._started:
            return
        self._started = True
        self._stopping = False
        logger.info(
            "[memory] tidy scheduler started (weekly Mon %s, monthly 1st %s, root=%s)",
            self._weekly_time,
            self._monthly_time,
            self._memory_root,
        )
        self._tasks.append(
            asyncio.create_task(self._catchup_task(), name="tidy-catchup")
        )
        self._tasks.append(
            asyncio.create_task(self._weekly_loop(), name="tidy-weekly")
        )
        self._tasks.append(
            asyncio.create_task(self._monthly_loop(), name="tidy-monthly")
        )

    async def _catchup_task(self) -> None:
        """startup catchup wrapper — runs in a worker thread (C-7)."""
        try:
            await asyncio.to_thread(self._catchup_all_sync, self._now())
        except Exception:
            logger.exception("[memory] tidy startup catchup failed")

    def _catchup_all_sync(self, now: datetime) -> None:
        """startup catchup: weekly oldest-first, then monthly (C-5).

        Monthly is skipped this round if weekly still has outstanding pending
        periods — the monthly report consumes weeklies, so running it across a
        weekly gap would build on missing input.
        """
        self._catchup_scope_sync("weekly", now)
        if self._stopping:
            return
        state = load_state(self._memory_root)
        if enumerate_pending_weeks(state.weekly_last, now):
            logger.warning(
                "[memory] monthly catchup skipped — weekly still behind "
                "(watermark=%s)",
                state.weekly_last,
            )
            return
        self._catchup_scope_sync("monthly", now)

    def _catchup_scope_sync(self, scope: Scope, now: datetime) -> None:
        """Catch up one scope's pending periods, oldest-first (C-1/C-4).

        Each period must fully succeed (:func:`tidy_succeeded`) before the
        watermark advances; the first non-success stops the scope so the next
        run retries from that period (C-3/C-4). The watermark is advanced
        atomically after each period (C-6). Cooperatively yields between
        periods when ``stop()`` was requested.
        """
        if not self._catchup_lock.acquire(blocking=False):
            logger.info(
                "[memory] %s catchup skipped — another catchup running", scope
            )
            return
        try:
            state = load_state(self._memory_root)
            if scope == "weekly":
                pending = enumerate_pending_weeks(state.weekly_last, now)
                fn = self._weekly_fn
                watermark = state.weekly_last
            else:
                pending = enumerate_pending_months(state.monthly_last, now)
                fn = self._monthly_fn
                watermark = state.monthly_last
            logger.info(
                "[memory] %s catchup: watermark=%s pending=%d %s",
                scope,
                watermark,
                len(pending),
                pending,
            )
            for period in pending:
                if self._stopping:
                    logger.info(
                        "[memory] %s catchup interrupted by stop (watermark=%s)",
                        scope,
                        watermark,
                    )
                    return
                started = self._now()
                try:
                    report = fn(period)
                except Exception:
                    logger.exception(
                        "[memory] %s tidy (%s) raised — watermark stays at %s, "
                        "stopping scope",
                        scope,
                        period,
                        watermark,
                    )
                    return
                elapsed = (self._now() - started).total_seconds()
                if not tidy_succeeded(report):
                    reason = _extract_failure(report) or "unknown"
                    logger.warning(
                        "[memory] %s tidy (%s) not succeeded (%s) — watermark "
                        "stays at %s, stopping scope",
                        scope,
                        period,
                        reason,
                        watermark,
                    )
                    return
                # success (incl. clean no-op): advance watermark atomically (C-6)
                stamp = self._now().isoformat()
                state = (
                    state.with_weekly(period, stamp)
                    if scope == "weekly"
                    else state.with_monthly(period, stamp)
                )
                save_state(self._memory_root, state)
                watermark = period
                logger.info(
                    "[memory] %s tidy (%s) done in %.1fs — watermark → %s",
                    scope,
                    period,
                    elapsed,
                    period,
                )
        finally:
            self._catchup_lock.release()

    async def stop(self) -> None:
        """Signal catchup to yield, then cancel all tasks.

        A tidy already running in a worker thread is not force-killed — tidy
        is re-runnable, so the next start resumes safely. ``_stopping`` makes
        the catchup loop exit between periods instead of grinding through the
        whole backlog on shutdown.
        """
        self._stopping = True
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
        # _stopping stays True: a catchup worker thread can outlive stop()
        # (asyncio cancel can't halt asyncio.to_thread's sync body), and it
        # checks _stopping between periods — keeping it True makes a lingering
        # thread exit at the next check instead of mutating memory after
        # teardown. start() resets it to False on the next launch.

    async def _weekly_loop(self) -> None:
        """Sleep until next Monday weekly_time, then catch up weekly pending."""
        while True:
            try:
                now = self._now()
                wait = seconds_until_next_weekday(now, _MONDAY, self._weekly_time)
                await self._sleep(wait)
            except asyncio.CancelledError:
                logger.info("[memory] weekly tidy loop cancelled")
                return
            await self._run_catchup_scope("weekly")

    async def _monthly_loop(self) -> None:
        """Sleep until next 1st monthly_time, then catch up monthly pending.

        Skipped while weekly is behind (C-5) — the monthly report consumes
        weeklies.
        """
        while True:
            try:
                now = self._now()
                wait = seconds_until_next_monthday(now, _FIRST, self._monthly_time)
                await self._sleep(wait)
            except asyncio.CancelledError:
                logger.info("[memory] monthly tidy loop cancelled")
                return
            state = load_state(self._memory_root)
            if enumerate_pending_weeks(state.weekly_last, self._now()):
                logger.warning(
                    "[memory] monthly trigger skipped — weekly still behind "
                    "(watermark=%s)",
                    state.weekly_last,
                )
                continue
            await self._run_catchup_scope("monthly")

    async def _run_catchup_scope(self, scope: Scope) -> None:
        """Run one scope's catchup in a worker thread; swallow failure (C-7/C-8)."""
        try:
            await asyncio.to_thread(self._catchup_scope_sync, scope, self._now())
        except Exception:
            logger.exception("[memory] %s catchup failed", scope)


def run_explicit_catchup(
    root: Path,
    scope: Scope,
    from_period: str,
    provider_factory: ProviderFactory,
    *,
    now: datetime | None = None,
) -> dict[str, object]:
    """CLI ``--from`` entry: catch up ``[from_period .. last completed]`` for
    one scope, advancing the watermark per success.

    Unlike the scheduled catchup this runs an explicit range regardless of the
    current watermark — safe because tidy is idempotent (stable plan id + apply
    snapshot replace on same-period rerun). Stops at the first non-success or
    raised exception, leaving the watermark wherever it got to (C-3).
    Default-deep history is NOT auto-traced (C-8); this is the explicit
    human-triggered door. Returns a structured report and never raises: a bad
    ``from_period`` or a tidy exception becomes ``error`` / ``failed_at``.
    """
    from trowel_py.memory.tidy import run_monthly_tidy, run_weekly_tidy

    now = now or datetime.now()
    state = load_state(root)
    watermark = state.weekly_last if scope == "weekly" else state.monthly_last
    # validate from_period format early — a typo returns a clean report, not a
    # ValueError traceback at the CLI.
    try:
        if scope == "weekly":
            next_iso_week(from_period)
        else:
            next_month(from_period)
    except ValueError as exc:
        return {
            "scope": scope,
            "from": from_period,
            "planned": [],
            "ran": [],
            "failed_at": None,
            "watermark": watermark,
            "error": f"bad --from period: {exc}",
        }

    if scope == "weekly":
        end = last_iso_week(now)
        cap = MAX_PENDING_WEEKS

        def fn(p: str) -> Any:
            return run_weekly_tidy(root, p, provider_factory())

        step = next_iso_week
    else:
        end = last_month(now)
        cap = MAX_PENDING_MONTHS

        def fn(p: str) -> Any:
            return run_monthly_tidy(root, p, provider_factory())

        step = next_month

    periods: list[str] = []
    cur = from_period
    while cur <= end and len(periods) < cap:
        periods.append(cur)
        cur = step(cur)

    ran: list[str] = []
    failed_at: str | None = None
    for period in periods:
        try:
            report = fn(period)
        except Exception:
            logger.exception(
                "[memory] %s catchup (%s) raised — watermark stays at %s",
                scope,
                period,
                watermark,
            )
            failed_at = period
            break
        if not tidy_succeeded(report):
            failed_at = period
            break
        advance_watermark(root, scope, period, now)
        ran.append(period)
        watermark = period

    return {
        "scope": scope,
        "from": from_period,
        "planned": periods,
        "ran": ran,
        "failed_at": failed_at,
        "watermark": watermark,
    }
