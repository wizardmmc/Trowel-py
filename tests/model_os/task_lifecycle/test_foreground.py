from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from pathlib import Path

import pytest

from tests.model_os.task_lifecycle.support import read_task_bundle
from trowel_py.model_os.store import (
    ForegroundConflict,
    ModelOsStore,
    TaskCommandError,
)
from trowel_py.model_os.types import TaskStatus, WorkItemStatus


def test_claim_foreground_sets_running_and_holds_claim(
    store: ModelOsStore,
) -> None:
    created = store.create_task_from_user_request(
        original_goal="整理资料",
        idempotency_key="foreground-task",
        authorization_scope="default",
    )
    store.promote_to_warm(created.task_id)
    store.claim_foreground(created.task_id)

    snapshot = store.read_snapshot()
    task, primary = read_task_bundle(snapshot, created.task_id)
    assert task.status == TaskStatus.RUNNING
    assert task.warm is True
    assert snapshot.foreground_task_id == created.task_id
    assert primary.status == WorkItemStatus.RUNNING


def test_claim_foreground_requires_warm(store: ModelOsStore) -> None:
    created = store.create_task_from_user_request(
        original_goal="整理资料",
        idempotency_key="cold-task",
        authorization_scope="default",
    )

    with pytest.raises(TaskCommandError):
        store.claim_foreground(created.task_id)


def test_concurrent_claim_foreground_only_one_winner(db_path: Path) -> None:
    # 必须使用两个独立连接竞争，才能覆盖真实的 SQLite 文件锁边界。
    with ExitStack() as stack:
        store_a = ModelOsStore(db_path)
        stack.callback(store_a.close)
        store_a.open()
        store_b = ModelOsStore(db_path)
        stack.callback(store_b.close)
        store_b.open()

        task_a = store_a.create_task_from_user_request(
            original_goal="整理资料甲",
            idempotency_key="task-a",
            authorization_scope="default",
        )
        task_b = store_b.create_task_from_user_request(
            original_goal="整理资料乙",
            idempotency_key="task-b",
            authorization_scope="default",
        )
        store_a.promote_to_warm(task_a.task_id)
        store_b.promote_to_warm(task_b.task_id)

        def claim(store: ModelOsStore, task_id: str) -> str:
            try:
                store.claim_foreground(task_id)
            except ForegroundConflict:
                return "conflict"
            return "ok"

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = {
                task_a.task_id: pool.submit(claim, store_a, task_a.task_id),
                task_b.task_id: pool.submit(claim, store_b, task_b.task_id),
            }
            results = {task_id: future.result() for task_id, future in futures.items()}

        winners = [task_id for task_id, result in results.items() if result == "ok"]
        assert len(winners) == 1
        assert list(results.values()).count("conflict") == 1

        snapshot = store_a.read_snapshot()
        assert snapshot.foreground_task_id == winners[0]
        loser_id = task_b.task_id if winners[0] == task_a.task_id else task_a.task_id
        loser, loser_primary = read_task_bundle(snapshot, loser_id)
        assert loser.status == TaskStatus.READY
        assert loser_primary.status == WorkItemStatus.READY
