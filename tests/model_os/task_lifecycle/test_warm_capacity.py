from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from pathlib import Path

import pytest

from tests.model_os.task_lifecycle.support import create_tasks, read_task_bundle
from trowel_py.model_os.store import ModelOsStore, WarmFull
from trowel_py.model_os.types import TaskStatus, WorkItemStatus


def test_fourth_promote_raises_warm_full_with_current_warm_ids(
    store: ModelOsStore,
) -> None:
    tasks = create_tasks(store, 4, prefix="capacity")
    for task in tasks[:3]:
        store.promote_to_warm(task.task_id)

    with pytest.raises(WarmFull) as raised:
        store.promote_to_warm(tasks[3].task_id)

    assert raised.value.limit == 3
    assert set(raised.value.warm_task_ids) == {task.task_id for task in tasks[:3]}
    rejected, rejected_primary = read_task_bundle(
        store.read_snapshot(),
        tasks[3].task_id,
    )
    assert rejected.status == TaskStatus.BACKLOG
    assert rejected.warm is False
    assert rejected_primary.status == WorkItemStatus.PENDING


def test_foreground_counts_against_warm_limit(store: ModelOsStore) -> None:
    tasks = create_tasks(store, 4, prefix="foreground-capacity")
    for task in tasks[:3]:
        store.promote_to_warm(task.task_id)
    store.claim_foreground(tasks[0].task_id)

    with pytest.raises(WarmFull):
        store.promote_to_warm(tasks[3].task_id)

    snapshot = store.read_snapshot()
    assert {task.task_id for task in snapshot.warm_tasks()} == {
        task.task_id for task in tasks[:3]
    }
    assert snapshot.foreground_task_id == tasks[0].task_id


def test_waiting_task_keeps_warm_slot(store: ModelOsStore) -> None:
    tasks = create_tasks(store, 4, prefix="waiting-capacity")
    for task in tasks[:3]:
        store.promote_to_warm(task.task_id)
    store.claim_foreground(tasks[0].task_id)
    store.set_waiting_user(
        tasks[0].task_id,
        cause="等待补充信息",
        correlation_id="question-1",
    )

    with pytest.raises(WarmFull):
        store.promote_to_warm(tasks[3].task_id)

    waiting, waiting_primary = read_task_bundle(
        store.read_snapshot(),
        tasks[0].task_id,
    )
    assert waiting.status == TaskStatus.WAITING_USER
    assert waiting.warm is True
    assert waiting_primary.status == WorkItemStatus.SUSPENDED


def test_concurrent_promote_never_exceeds_limit(db_path: Path) -> None:
    # 每个竞争者使用独立连接，线程异常必须经 Future 传播。
    with ExitStack() as stack:
        stores = [ModelOsStore(db_path, warm_limit=2) for _ in range(4)]
        for candidate_store in stores:
            stack.callback(candidate_store.close)
            candidate_store.open()

        tasks = create_tasks(stores[0], 4, prefix="concurrent-capacity")

        def promote(candidate_store: ModelOsStore, task_id: str) -> str:
            try:
                candidate_store.promote_to_warm(task_id)
            except WarmFull:
                return "full"
            return "ok"

        with ThreadPoolExecutor(max_workers=4) as pool:
            futures = {
                task.task_id: pool.submit(promote, candidate_store, task.task_id)
                for task, candidate_store in zip(tasks, stores, strict=True)
            }
            results = {task_id: future.result() for task_id, future in futures.items()}

        winners = {task_id for task_id, result in results.items() if result == "ok"}
        losers = set(results) - winners
        assert len(winners) == 2
        assert list(results.values()).count("full") == 2

        snapshot = stores[0].read_snapshot()
        assert {task.task_id for task in snapshot.warm_tasks()} == winners
        for task_id in winners:
            task, primary = read_task_bundle(snapshot, task_id)
            assert task.status == TaskStatus.READY
            assert primary.status == WorkItemStatus.READY
        for task_id in losers:
            task, primary = read_task_bundle(snapshot, task_id)
            assert task.status == TaskStatus.BACKLOG
            assert task.warm is False
            assert primary.status == WorkItemStatus.PENDING
