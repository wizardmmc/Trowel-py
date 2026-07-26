from __future__ import annotations

from datetime import datetime, timezone

import pytest

from trowel_py.model_os.incubation import (
    IncubationError,
    IncubationPlanStatus,
    IncubationRepository,
)
from trowel_py.model_os.store import ModelOsStore
from trowel_py.model_os.types import TaskStatus, WorkItemKind, WorkItemStatus
from trowel_py.model_os.waking import WakeConditionKind, WakeObservation

from tests.model_os.incubation.support import create_command, prepared_running_task


def test_create_plan_atomically_suspends_task_and_creates_incubation_work_item(
    store: ModelOsStore,
) -> None:
    task, ref = prepared_running_task(store)
    repository = IncubationRepository(store)

    plan = repository.create_plan(create_command(task.task_id, ref))

    assert plan.status is IncubationPlanStatus.PENDING_WAKE
    assert plan.cycle == 0
    assert plan.max_scheduled_cycles == 1
    assert plan.prepared_snapshot_ref == ref
    snapshot = store.read_snapshot()
    current_task = next(item for item in snapshot.tasks if item.task_id == task.task_id)
    assert current_task.status is TaskStatus.INCUBATING
    work_item = next(
        item for item in snapshot.work_items if item.work_item_id == plan.work_item_id
    )
    assert work_item.kind is WorkItemKind.INCUBATION
    assert work_item.task_id == task.task_id
    assert work_item.status is WorkItemStatus.PENDING
    assert snapshot.episodes == tuple(
        item for item in snapshot.episodes if item.episode_id == ref.episode_id
    )


def test_create_plan_is_idempotent_and_conflicting_reuse_changes_nothing(
    store: ModelOsStore,
) -> None:
    task, ref = prepared_running_task(store)
    repository = IncubationRepository(store)
    command = create_command(task.task_id, ref)

    first = repository.create_plan(command)
    assert repository.create_plan(command) == first
    conflict = create_command(task.task_id, ref, command_id=command.command_id)
    conflict = conflict.__class__(
        **{**conflict.__dict__, "unresolved_question": "另一个问题"}
    )

    with pytest.raises(IncubationError) as raised:
        repository.create_plan(conflict)

    assert raised.value.code == "idempotency_conflict"
    assert repository.plan_count() == 1


def test_command_id_cannot_be_reused_for_a_different_command_kind(
    store: ModelOsStore,
) -> None:
    task, ref = prepared_running_task(store)
    repository = IncubationRepository(store)
    command = create_command(task.task_id, ref, command_id="global-command")
    plan = repository.create_plan(command)

    with pytest.raises(IncubationError) as raised:
        repository.cancel_plan(
            plan.plan_id,
            command_id=command.command_id,
            occurred_at=datetime(2026, 7, 26, 9, 0, tzinfo=timezone.utc),
        )

    assert raised.value.code == "idempotency_conflict"
    assert repository.get_plan(plan.plan_id).status is IncubationPlanStatus.PENDING_WAKE
    current = next(
        item for item in store.read_snapshot().tasks if item.task_id == task.task_id
    )
    assert current.status is TaskStatus.INCUBATING


def test_snapshot_from_another_task_is_rejected_without_partial_state(
    store: ModelOsStore,
) -> None:
    first, _ = prepared_running_task(store, key="first")
    store.release_foreground()
    second, second_ref = prepared_running_task(store, key="second")
    store.release_foreground()
    store.claim_foreground(first.task_id)
    repository = IncubationRepository(store)

    with pytest.raises(IncubationError) as raised:
        repository.create_plan(create_command(first.task_id, second_ref))

    assert raised.value.code == "snapshot_task_mismatch"
    assert repository.plan_count() == 0
    snapshot = store.read_snapshot()
    assert next(item for item in snapshot.tasks if item.task_id == first.task_id).status is TaskStatus.RUNNING
    assert next(item for item in snapshot.tasks if item.task_id == second.task_id).status is TaskStatus.READY


def test_wake_claim_is_idempotent_and_keeps_task_incubating(
    store: ModelOsStore,
) -> None:
    task, ref = prepared_running_task(store)
    repository = IncubationRepository(store)
    plan = repository.create_plan(create_command(task.task_id, ref))
    observation = WakeObservation(
        observation_id="timer-plan-1",
        kind=WakeConditionKind.TIME,
        target_ref="clock",
        observed_at="2026-07-27T08:00:01+00:00",
        source="timer",
        details={},
    )

    first = repository.consume_wake(observation)
    second = repository.consume_wake(observation)

    assert first == second
    assert len(first) == 1
    assert first[0].condition_id == f"incubation:{plan.plan_id}"
    ready = repository.get_plan(plan.plan_id)
    assert ready.status is IncubationPlanStatus.READY
    assert ready.cycle == 1
    current_task = next(
        item for item in store.read_snapshot().tasks if item.task_id == task.task_id
    )
    assert current_task.status is TaskStatus.INCUBATING
    assert repository.wake_count() == 1


def test_cancel_pending_plan_restores_task_without_model_work(
    store: ModelOsStore,
) -> None:
    task, ref = prepared_running_task(store)
    repository = IncubationRepository(store)
    plan = repository.create_plan(create_command(task.task_id, ref))

    cancelled = repository.cancel_plan(
        plan.plan_id,
        command_id="cancel-1",
        occurred_at=datetime(2026, 7, 26, 9, 0, tzinfo=timezone.utc),
    )

    assert cancelled.status is IncubationPlanStatus.CANCELLED
    assert cancelled.stop_reason == "user_cancelled"
    snapshot = store.read_snapshot()
    current_task = next(item for item in snapshot.tasks if item.task_id == task.task_id)
    work_item = next(
        item for item in snapshot.work_items if item.work_item_id == plan.work_item_id
    )
    assert current_task.status is TaskStatus.READY
    assert work_item.status is WorkItemStatus.CANCELLED
    assert repository.candidate_count() == 0
