"""应用内 tidy 调度器及持久补跑生命周期。"""

from __future__ import annotations

import asyncio
import logging
import threading
from datetime import datetime, time
from pathlib import Path
from typing import Any

from trowel_py.memory.tidy_state import (
    enumerate_pending_months,
    enumerate_pending_weeks,
    load_state,
)

from .report import _extract_failure, tidy_succeeded
from .timing import seconds_until_next_monthday, seconds_until_next_weekday
from .types import (
    DEFAULT_MONTHLY_TIME,
    DEFAULT_WEEKLY_TIME,
    NowFn,
    ProviderFactory,
    Scope,
    SleepFn,
    TidyFn,
    _FIRST,
    _MONDAY,
)

logger = logging.getLogger("trowel_py.memory.tidy_scheduler")


def _save_state(root: Path, state: Any) -> None:
    """经稳定入口写水位，保留既有 monkeypatch seam。"""
    import trowel_py.memory.tidy_scheduler as facade

    facade.save_state(root, state)


class TidyScheduler:
    """管理启动补跑、周/月定时循环和协作式停止。"""

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
        self._catchup_lock = threading.Lock()

    def _default_weekly(self, iso_week: str) -> Any:
        from trowel_py.memory.tidy import run_weekly_tidy

        return run_weekly_tidy(
            self._memory_root,
            iso_week,
            self._provider_factory(),
        )

    def _default_monthly(self, month: str) -> Any:
        from trowel_py.memory.tidy import run_monthly_tidy

        return run_monthly_tidy(
            self._memory_root,
            month,
            self._provider_factory(),
        )

    @property
    def tasks(self) -> tuple[asyncio.Task[None], ...]:
        return tuple(self._tasks)

    async def start(self) -> None:
        """幂等启动补跑任务和两个定时循环，不等待同步补跑完成。"""
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
        self._tasks.append(asyncio.create_task(self._weekly_loop(), name="tidy-weekly"))
        self._tasks.append(
            asyncio.create_task(self._monthly_loop(), name="tidy-monthly")
        )

    async def _catchup_task(self) -> None:
        try:
            await asyncio.to_thread(self._catchup_all_sync, self._now())
        except Exception:
            logger.exception("[memory] tidy startup catchup failed")

    def _catchup_all_sync(self, now: datetime) -> None:
        """先补 weekly；仍有缺口时不生成依赖 weekly 的 monthly。"""
        self._catchup_scope_sync("weekly", now)
        if self._stopping:
            return
        state = load_state(self._memory_root)
        if enumerate_pending_weeks(state.weekly_last, now):
            logger.warning(
                "[memory] monthly catchup skipped — weekly still behind (watermark=%s)",
                state.weekly_last,
            )
            return
        self._catchup_scope_sync("monthly", now)

    def _catchup_scope_sync(self, scope: Scope, now: datetime) -> None:
        """串行补跑一个 scope，首个失败停止，每次成功后持久化水位。"""
        if not self._catchup_lock.acquire(blocking=False):
            logger.info(
                "[memory] %s catchup skipped — another catchup running",
                scope,
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
                stamp = self._now().isoformat()
                state = (
                    state.with_weekly(period, stamp)
                    if scope == "weekly"
                    else state.with_monthly(period, stamp)
                )
                _save_state(self._memory_root, state)
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
        """取消 asyncio task，并让仍在线程中的补跑在周期之间退出。"""
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
        # to_thread 中已开始的同步调用不能被 task.cancel() 终止，因此这里必须
        # 保持 True，让残留线程在下一个 period 前退出；下次 start() 再复位。

    async def _weekly_loop(self) -> None:
        while True:
            try:
                now = self._now()
                wait = seconds_until_next_weekday(
                    now,
                    _MONDAY,
                    self._weekly_time,
                )
                await self._sleep(wait)
            except asyncio.CancelledError:
                logger.info("[memory] weekly tidy loop cancelled")
                return
            await self._run_catchup_scope("weekly")

    async def _monthly_loop(self) -> None:
        while True:
            try:
                now = self._now()
                wait = seconds_until_next_monthday(
                    now,
                    _FIRST,
                    self._monthly_time,
                )
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
        try:
            await asyncio.to_thread(
                self._catchup_scope_sync,
                scope,
                self._now(),
            )
        except Exception:
            logger.exception("[memory] %s catchup failed", scope)
