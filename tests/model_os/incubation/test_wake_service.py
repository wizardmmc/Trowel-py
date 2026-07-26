from __future__ import annotations

from dataclasses import replace

import pytest

from trowel_py.model_os.incubation import IncubationPlanStatus, IncubationRepository
from trowel_py.model_os.types import TaskStatus
from trowel_py.model_os.waking import WakeConditionKind
from trowel_py.model_os.waking.observers import SystemObserver
from trowel_py.model_os.waking.runtime import WakeService

from tests.model_os.incubation.support import create_command, prepared_running_task


@pytest.mark.anyio
async def test_existing_wake_service_scans_and_dispatches_incubation(store) -> None:
    task, ref = prepared_running_task(store)
    repository = IncubationRepository(store)
    plan = repository.create_plan(create_command(task.task_id, ref))
    seen = []

    async def on_wake(event):
        seen.append(event)

    service = WakeService(
        store,
        observer=SystemObserver(),
        now=lambda: "2026-07-27T08:00:01+00:00",
        host_detector=None,
        condition_providers=(repository.wake_conditions,),
        observation_consumers=(repository.consume_wake,),
        on_wake=on_wake,
    )

    first = await service.run_once()
    second = await service.run_once()

    assert len(first) == 1
    assert first[0].condition_id == f"incubation:{plan.plan_id}"
    assert seen == list(first)
    assert second == ()
    assert repository.get_plan(plan.plan_id).status is IncubationPlanStatus.READY


@pytest.mark.anyio
async def test_existing_wake_service_expires_event_plan_at_its_deadline(store) -> None:
    task, ref = prepared_running_task(store)
    repository = IncubationRepository(store)
    command = create_command(task.task_id, ref)
    plan = repository.create_plan(
        replace(
            command,
            wake_condition=replace(
                command.wake_condition,
                kind=WakeConditionKind.OBSERVED_STATE,
                target_ref="process:999999",
                match_params={"state_kind": "process", "state": "exited"},
                due_at=None,
            ),
        )
    )
    service = WakeService(
        store,
        observer=SystemObserver(),
        now=lambda: "2026-07-28T08:00:01+00:00",
        host_detector=None,
        condition_providers=(repository.wake_conditions,),
        observation_consumers=(repository.consume_wake,),
    )

    assert await service.run_once() == ()

    stopped = repository.get_plan(plan.plan_id)
    assert stopped.status is IncubationPlanStatus.STOPPED
    assert stopped.stop_reason == "deadline_expired"
    current = next(
        item for item in store.read_snapshot().tasks if item.task_id == task.task_id
    )
    assert current.status is TaskStatus.READY
