from __future__ import annotations

import pytest

from trowel_py.model_os.scheduling import AttentionScheduler, ScheduleReason
from trowel_py.model_os.workbench import set_automation_paused


@pytest.mark.asyncio
async def test_paused_automation_does_not_dispatch_ready_work(store) -> None:
    task = store.create_task_from_user_request(
        original_goal="等待人工调度",
        idempotency_key="paused-task",
    )
    store.promote_to_warm(task.task_id)
    set_automation_paused(store, paused=True, idempotency_key="pause-scheduler")
    scheduler = AttentionScheduler(
        store,
        request_user_preempt=lambda _current, _target: None,
    )

    outcome = await scheduler.trigger("workbench.pause.probe")

    assert outcome.decision.reason is ScheduleReason.AUTOMATION_PAUSED
    assert outcome.result_code == "automation_paused"
    assert store.read_foreground_task_id() is None


@pytest.mark.asyncio
async def test_user_foreground_request_still_reaches_scheduler_while_paused(store) -> None:
    task = store.create_task_from_user_request(
        original_goal="人工选择的任务",
        idempotency_key="manual-task",
    )
    store.promote_to_warm(task.task_id)
    set_automation_paused(store, paused=True, idempotency_key="pause-before-manual")
    scheduler = AttentionScheduler(
        store,
        request_user_preempt=lambda _current, _target: None,
    )

    outcome = await scheduler.request_foreground(
        task.task_id,
        idempotency_key="manual-foreground",
    )

    assert outcome.decision.reason is ScheduleReason.USER_OVERRIDE_SWITCH
