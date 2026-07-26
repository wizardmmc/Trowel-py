"""应用生命周期内扫描已登记条件；循环本身不调用模型或领取 WorkLease。"""

from __future__ import annotations

import asyncio
import hashlib
import logging
from collections.abc import Awaitable, Callable

from trowel_py.model_os.waking.matcher import condition_from_waiting
from trowel_py.model_os.waking.models import (
    WakeConditionKind,
    WakeEvent,
    WakeObservation,
)
from trowel_py.model_os.waking.observers import HostSuspendDetector, SystemObserver

logger = logging.getLogger(__name__)


class WakeService:
    def __init__(
        self,
        store,
        *,
        observer: SystemObserver,
        now: Callable[[], str],
        host_detector: HostSuspendDetector | None,
        interval_seconds: float = 30.0,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._store = store
        self._observer = observer
        self._now = now
        self._host_detector = host_detector
        self._interval_seconds = interval_seconds
        self._sleep = sleep
        self._task: asyncio.Task[None] | None = None

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    @property
    def model_calls(self) -> int:
        return 0

    async def start(self) -> None:
        if self.running:
            return
        await self.run_once()
        self._task = asyncio.create_task(self._loop(), name="model-os-wake")

    async def stop(self) -> None:
        task = self._task
        self._task = None
        if task is None:
            return
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    async def _loop(self) -> None:
        while True:
            try:
                await self._sleep(self._interval_seconds)
                await self.run_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.warning("[model-os] wake scan failed", exc_info=True)

    async def run_once(self) -> tuple[WakeEvent, ...]:
        observed_at = self._now()
        snapshot = self._store.read_snapshot()
        conditions = []
        for task in snapshot.tasks:
            if task.waiting_condition is None:
                continue
            condition = condition_from_waiting(task.task_id, task.waiting_condition)
            if condition is not None:
                conditions.append(condition)
        consumed: list[WakeEvent] = []
        host_event = None
        if self._host_detector is not None:
            try:
                host_event = self._host_detector.poll()
            except Exception:
                logger.warning(
                    "[model-os] host suspend observer unavailable", exc_info=True
                )
                self._host_detector = None
        host_targets: set[str] = set()
        for condition in conditions:
            try:
                observation = self._observer.observe(
                    condition, observed_at=observed_at
                )
            except Exception:
                logger.warning(
                    "[model-os] wake observer failed for condition %s",
                    condition.condition_id,
                    exc_info=True,
                )
                continue
            if observation is not None:
                consumed.extend(self._store.consume_wake(observation))
            if (
                host_event is not None
                and condition.kind is WakeConditionKind.HOST_EVENT
                and condition.target_ref not in host_targets
            ):
                host_targets.add(condition.target_ref)
                identity = hashlib.sha256(
                    f"{host_event}\0{observed_at}".encode()
                ).hexdigest()
                consumed.extend(
                    self._store.consume_wake(
                        WakeObservation(
                            observation_id=f"host:{identity}",
                            kind=WakeConditionKind.HOST_EVENT,
                            target_ref=condition.target_ref,
                            observed_at=observed_at,
                            source="host-observer",
                            details={"event": host_event},
                        )
                    )
                )
        return tuple(dict.fromkeys(consumed))
