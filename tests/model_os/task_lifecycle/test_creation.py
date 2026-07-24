from pathlib import Path

from trowel_py.model_os.store import ModelOsStore
from trowel_py.model_os.types import (
    TaskOrigin,
    TaskStatus,
    WorkItemKind,
    WorkItemStatus,
)


def test_create_task_creates_primary_work_item(store: ModelOsStore) -> None:
    task = store.create_task_from_user_request(
        original_goal="整理项目文档",
        idempotency_key="create-task",
        authorization_scope="default",
    )

    assert task.original_goal == "整理项目文档"
    assert task.status == TaskStatus.BACKLOG
    assert task.appended_constraints == ()

    snapshot = store.read_snapshot()
    primary = [
        item
        for item in snapshot.work_items
        if item.task_id == task.task_id and item.kind == WorkItemKind.TASK
    ]
    assert len(primary) == 1
    assert task.primary_work_item_id == primary[0].work_item_id
    # Task backlog 对应主 WorkItem pending。
    assert primary[0].status == WorkItemStatus.PENDING


def test_create_task_idempotent_on_key(store: ModelOsStore) -> None:
    first = store.create_task_from_user_request(
        original_goal="整理资料",
        idempotency_key="same-request",
        authorization_scope="default",
    )
    second = store.create_task_from_user_request(
        original_goal="整理资料",
        idempotency_key="same-request",
        authorization_scope="default",
    )

    assert first.task_id == second.task_id
    snapshot = store.read_snapshot()
    primary = [
        item
        for item in snapshot.work_items
        if item.task_id == first.task_id and item.kind == WorkItemKind.TASK
    ]
    assert len(primary) == 1
    assert first.primary_work_item_id == primary[0].work_item_id


def test_restart_preserves_task_and_foreground(db_path: Path) -> None:
    first_store = ModelOsStore(db_path)
    first_store.open()
    try:
        created = first_store.create_task_from_user_request(
            original_goal="整理项目文档",
            idempotency_key="restart-task",
            authorization_scope="default",
        )
        first_store.promote_to_warm(created.task_id)
        first_store.claim_foreground(created.task_id)
    finally:
        first_store.close()

    second_store = ModelOsStore(db_path)
    second_store.open()
    try:
        snapshot = second_store.read_snapshot()
        restored = next(
            task for task in snapshot.tasks if task.task_id == created.task_id
        )
        primary = [
            item
            for item in snapshot.work_items
            if item.task_id == created.task_id and item.kind == WorkItemKind.TASK
        ]

        assert restored.original_goal == "整理项目文档"
        assert restored.origin == TaskOrigin.USER_REQUEST
        assert restored.status == TaskStatus.RUNNING
        assert restored.warm is True
        assert snapshot.foreground_task_id == created.task_id
        assert len(primary) == 1
        assert restored.primary_work_item_id == primary[0].work_item_id
        assert primary[0].status == WorkItemStatus.RUNNING
    finally:
        second_store.close()
