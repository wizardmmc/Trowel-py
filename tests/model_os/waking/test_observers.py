from __future__ import annotations

import asyncio
import subprocess
import sys
from pathlib import Path

import pytest

from tests.model_os.waking.support import running_task
from trowel_py.model_os.store import ModelOsStore
from trowel_py.model_os.types import TaskStatus
from trowel_py.model_os.waking import condition_from_waiting
from trowel_py.model_os.waking.observers import (
    HostClockSample,
    HostSuspendDetector,
    SystemObserver,
    read_process_start_identity,
)
from trowel_py.model_os.waking.runtime import WakeService


def _condition(store: ModelOsStore, task_id: str):
    task = next(item for item in store.read_snapshot().tasks if item.task_id == task_id)
    assert task.waiting_condition is not None
    condition = condition_from_waiting(task_id, task.waiting_condition)
    assert condition is not None
    return condition


def test_real_file_observer_wakes_when_exact_file_exists(
    store: ModelOsStore, tmp_path: Path
) -> None:
    target = tmp_path / "build.done"
    task = running_task(store)
    store.set_waiting_event(
        task.task_id,
        cause="等待文件",
        condition_kind="file",
        target_ref=f"file:{target}",
        match_params={"state": "exists"},
    )
    target.touch()

    observation = SystemObserver().observe(
        _condition(store, task.task_id),
        observed_at="2026-07-26T09:00:00Z",
    )

    assert observation is not None
    assert observation.details["state"] == "exists"
    store.consume_wake(observation)
    assert store.read_snapshot().tasks[0].status is TaskStatus.READY


def test_real_short_process_observer_uses_start_identity(
    store: ModelOsStore,
) -> None:
    process = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(0.15)"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        identity = read_process_start_identity(process.pid)
        assert identity is not None
        task = running_task(store)
        store.set_waiting_event(
            task.task_id,
            cause="等待进程退出",
            condition_kind="process",
            target_ref=f"process:{process.pid}",
            match_params={"state": "exited", "start_identity": identity},
        )
        process.wait(timeout=5)

        observation = SystemObserver().observe(
            _condition(store, task.task_id),
            observed_at="2026-07-26T09:00:00Z",
        )

        assert observation is not None
        assert observation.details == {
            "state_kind": "process",
            "state": "exited",
            "start_identity": identity,
        }
        store.consume_wake(observation)
        assert store.read_snapshot().tasks[0].status is TaskStatus.READY
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()


def test_process_observer_refuses_condition_without_start_identity(
    store: ModelOsStore,
) -> None:
    task = running_task(store)
    store.set_waiting_event(
        task.task_id,
        cause="等待进程退出",
        condition_kind="process",
        target_ref="process:999999",
        match_params={"state": "exited"},
    )

    assert (
        SystemObserver().observe(
            _condition(store, task.task_id),
            observed_at="2026-07-26T09:00:00Z",
        )
        is None
    )


def test_host_suspend_detector_uses_continuous_minus_active_time() -> None:
    samples = iter(
        [
            HostClockSample("boot-1", 10_000_000_000, 10_000_000_000),
            HostClockSample("boot-1", 11_000_000_000, 3_611_000_000_000),
            HostClockSample("boot-1", 12_000_000_000, 3_612_000_000_000),
        ]
    )
    detector = HostSuspendDetector(lambda: next(samples), minimum_suspend_seconds=1.0)

    assert detector.poll() is None
    assert detector.poll() == "wake"
    assert detector.poll() is None


def test_host_suspend_detector_does_not_treat_clock_rollback_as_wake() -> None:
    samples = iter(
        [
            HostClockSample("boot-1", 10, 10),
            HostClockSample("boot-1", 9, 1000),
        ]
    )
    detector = HostSuspendDetector(lambda: next(samples), minimum_suspend_seconds=0)

    assert detector.poll() is None
    assert detector.poll() is None


@pytest.mark.anyio
async def test_due_time_catchup_is_merged_once(store: ModelOsStore) -> None:
    task = running_task(store)
    store.set_waiting_event(
        task.task_id,
        cause="明早继续",
        condition_kind="time",
        target_ref="timer:morning",
        deadline="2026-07-26T09:00:00Z",
    )
    service = WakeService(
        store,
        observer=SystemObserver(),
        now=lambda: "2026-07-26T12:00:00Z",
        host_detector=None,
    )

    first = await service.run_once()
    second = await service.run_once()

    assert len(first) == 1
    assert second == ()
    assert len(
        [event for _, event in store.list_events() if event.kind == "wake.consumed"]
    ) == 1


@pytest.mark.anyio
async def test_24h_without_conditions_has_no_wake_or_background_work(
    store: ModelOsStore,
) -> None:
    service = WakeService(
        store,
        observer=SystemObserver(),
        now=lambda: "2026-07-26T12:00:00Z",
        host_detector=None,
    )

    for _ in range(24 * 60):
        assert await service.run_once() == ()

    assert store.list_events() == []
    assert service.model_calls == 0


@pytest.mark.anyio
async def test_service_start_and_stop_leave_no_background_task(
    store: ModelOsStore,
) -> None:
    entered = asyncio.Event()

    async def wait_forever(_seconds: float) -> None:
        entered.set()
        await asyncio.Event().wait()

    service = WakeService(
        store,
        observer=SystemObserver(),
        now=lambda: "2026-07-26T12:00:00Z",
        host_detector=None,
        sleep=wait_forever,
    )

    await service.start()
    await entered.wait()
    assert service.running
    await service.stop()
    assert not service.running


@pytest.mark.anyio
async def test_host_observer_failure_does_not_block_due_timer(
    store: ModelOsStore,
) -> None:
    task = running_task(store)
    store.set_waiting_event(
        task.task_id,
        cause="定时继续",
        condition_kind="time",
        target_ref="timer:once",
        deadline="2026-07-26T09:00:00Z",
    )

    class FailingHostDetector:
        def poll(self):
            raise RuntimeError("host clock unavailable")

    service = WakeService(
        store,
        observer=SystemObserver(),
        now=lambda: "2026-07-26T12:00:00Z",
        host_detector=FailingHostDetector(),
    )

    events = await service.run_once()

    assert len(events) == 1
    assert store.read_snapshot().tasks[0].status is TaskStatus.READY


@pytest.mark.anyio
async def test_one_condition_observer_failure_does_not_block_others(
    store: ModelOsStore,
) -> None:
    first = running_task(store, "wake-first")
    store.set_waiting_event(
        first.task_id,
        cause="坏条件",
        condition_kind="file",
        target_ref="file:/tmp/bad",
        match_params={"state": "exists"},
    )
    second = running_task(store, "wake-second")
    store.set_waiting_event(
        second.task_id,
        cause="定时继续",
        condition_kind="time",
        target_ref="timer:once",
        deadline="2026-07-26T09:00:00Z",
    )

    class PartlyFailingObserver(SystemObserver):
        def observe(self, condition, *, observed_at):
            if condition.task_id == first.task_id:
                raise RuntimeError("condition observer unavailable")
            return super().observe(condition, observed_at=observed_at)

    service = WakeService(
        store,
        observer=PartlyFailingObserver(),
        now=lambda: "2026-07-26T12:00:00Z",
        host_detector=None,
    )

    events = await service.run_once()

    assert len(events) == 1
    snapshot = store.read_snapshot()
    statuses = {task.task_id: task.status for task in snapshot.tasks}
    assert statuses[first.task_id] is TaskStatus.WAITING_EVENT
    assert statuses[second.task_id] is TaskStatus.READY
