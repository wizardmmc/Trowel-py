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
    """经包级公开入口保存水位，使入口上的替代实现也对运行时生效。

    Args:
        root: 记忆目录。
        state: 要保存的 Tidy 状态。
    """
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
        """配置 Tidy 调度器但不启动任务。

        Args:
            memory_root: 记忆目录。
            provider_factory: 默认周月 Tidy 每次调用时使用的提供者工厂；注入
                ``weekly_fn`` 或 ``monthly_fn`` 后，对应路径不使用该工厂。
            weekly_time: 每周一触发周补跑的本机时间。
            monthly_time: 每月一日触发月补跑的本机时间。
            now_fn: 当前时间来源；省略时使用 ``datetime.now``。
            sleep_fn: 定时循环的异步等待函数；省略时使用 ``asyncio.sleep``。
            weekly_fn: 接收 ISO 周的同步 Tidy 函数。
            monthly_fn: 接收月份的同步 Tidy 函数。
        """
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
        """调用提供者工厂并运行一个默认周周期。

        Args:
            iso_week: 要整理的 ISO 周。

        Returns:
            ``run_weekly_tidy`` 的原始报告。
        """
        from trowel_py.memory.tidy import run_weekly_tidy

        return run_weekly_tidy(
            self._memory_root,
            iso_week,
            self._provider_factory(),
        )

    def _default_monthly(self, month: str) -> Any:
        """调用提供者工厂并运行一个默认月周期。

        Args:
            month: 要整理的月份。

        Returns:
            ``run_monthly_tidy`` 的原始报告。
        """
        from trowel_py.memory.tidy import run_monthly_tidy

        return run_monthly_tidy(
            self._memory_root,
            month,
            self._provider_factory(),
        )

    @property
    def tasks(self) -> tuple[asyncio.Task[None], ...]:
        """返回当前持有的异步任务引用快照。"""
        return tuple(self._tasks)

    async def start(self) -> None:
        """幂等启动一次补跑任务及周、月两个定时循环。

        方法只创建任务，不等待启动补跑完成。已处于 started 状态时直接返回。
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
        self._tasks.append(asyncio.create_task(self._weekly_loop(), name="tidy-weekly"))
        self._tasks.append(
            asyncio.create_task(self._monthly_loop(), name="tidy-monthly")
        )

    async def _catchup_task(self) -> None:
        """在线程中执行启动补跑，并记录除取消外的顶层异常。"""
        try:
            await asyncio.to_thread(self._catchup_all_sync, self._now())
        except Exception:
            logger.exception("[memory] tidy startup catchup failed")

    def _catchup_all_sync(self, now: datetime) -> None:
        """先补周水位，确认无周缺口后再补月水位。

        Args:
            now: 两个范围共用的已完成周期截止时间。
        """
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

    async def _weekly_loop(self) -> None:
        """每周一到点触发周补跑，等待被取消时正常退出。

        读取当前时间、计算下次触发或等待期间的非取消异常会结束循环；补跑顶层
        异常由 ``_run_catchup_scope`` 记录后，下一周仍会继续调度。
        """
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
        """每月一日到点触发月补跑，但要求周水位已经追平。

        周水位落后时跳过本次月触发，直到下个月定时点才再次检查。读取当前时间、
        计算或等待下次触发，以及读取或检查周水位时的非取消异常会结束循环；
        月补跑顶层异常由 ``_run_catchup_scope`` 记录后，下一月仍会继续调度。
        """
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
        """在线程中补跑一个范围，并记录除取消外的顶层异常。

        Args:
            scope: ``weekly`` 或 ``monthly``。
        """
        try:
            await asyncio.to_thread(
                self._catchup_scope_sync,
                scope,
                self._now(),
            )
        except Exception:
            logger.exception("[memory] %s catchup failed", scope)
