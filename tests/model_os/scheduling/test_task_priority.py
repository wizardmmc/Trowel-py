import pytest

from trowel_py.model_os.store import ModelOsStore, TaskCommandError
from trowel_py.model_os.types import EventKind, Provenance


def test_user_can_change_task_priority_with_an_idempotency_key(
    store: ModelOsStore,
) -> None:
    task = store.create_task_from_user_request(
        original_goal="整理匿名材料",
        idempotency_key="create-priority-task",
    )

    store.set_task_priority(task.task_id, priority=7, idempotency_key="priority-1")
    store.set_task_priority(task.task_id, priority=7, idempotency_key="priority-1")

    snapshot = store.read_snapshot()
    changed = [
        event
        for _, event in store.list_events()
        if event.kind == EventKind.TASK_PRIORITY_CHANGED
    ]
    assert (
        next(item for item in snapshot.tasks if item.task_id == task.task_id).priority
        == 7
    )
    assert len(changed) == 1
    assert changed[0].provenance is Provenance.USER_DECISION
    assert changed[0].payload["priority"] == 7


def test_priority_idempotency_key_cannot_be_reused_for_different_content(
    store: ModelOsStore,
) -> None:
    first = store.create_task_from_user_request(
        original_goal="任务一",
        idempotency_key="create-priority-first",
    )
    second = store.create_task_from_user_request(
        original_goal="任务二",
        idempotency_key="create-priority-second",
    )
    store.set_task_priority(first.task_id, priority=1, idempotency_key="priority-key")

    with pytest.raises(TaskCommandError, match="different content"):
        store.set_task_priority(
            second.task_id, priority=1, idempotency_key="priority-key"
        )
    with pytest.raises(TaskCommandError, match="different content"):
        store.set_task_priority(
            first.task_id, priority=2, idempotency_key="priority-key"
        )


def test_priority_command_rejects_blank_key_and_terminal_task(
    store: ModelOsStore,
) -> None:
    task = store.create_task_from_user_request(
        original_goal="终态任务",
        idempotency_key="create-terminal-priority",
    )

    with pytest.raises(TaskCommandError, match="idempotency_key"):
        store.set_task_priority(task.task_id, priority=1, idempotency_key=" ")

    store.promote_to_warm(task.task_id)
    store.claim_foreground(task.task_id)
    store.cancel_task(task.task_id, reason="结束")
    with pytest.raises(TaskCommandError, match="terminal"):
        store.set_task_priority(task.task_id, priority=1, idempotency_key="after-end")


def test_priority_retry_returns_original_result_after_task_becomes_terminal(
    store: ModelOsStore,
) -> None:
    task = store.create_task_from_user_request(
        original_goal="终态前修改优先级",
        idempotency_key="create-priority-retry",
    )
    store.set_task_priority(task.task_id, priority=3, idempotency_key="priority-retry")
    store.promote_to_warm(task.task_id)
    store.claim_foreground(task.task_id)
    store.cancel_task(task.task_id, reason="结束")

    store.set_task_priority(task.task_id, priority=3, idempotency_key="priority-retry")

    changed = [
        event
        for _, event in store.list_events()
        if event.kind == EventKind.TASK_PRIORITY_CHANGED
    ]
    assert len(changed) == 1
