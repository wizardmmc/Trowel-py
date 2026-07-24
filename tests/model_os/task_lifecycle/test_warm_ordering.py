from itertools import chain, repeat

import pytest

import trowel_py.model_os.store as store_module
from tests.model_os.task_lifecycle.support import create_tasks, read_task_bundle
from trowel_py.model_os.store import ModelOsStore, TaskCommandError
from trowel_py.model_os.types import TaskStatus, WorkItemStatus


def test_warm_order_defaults_to_created_at(
    store: ModelOsStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # 创建时间顺序必须不同于插入顺序，避免原样返回实现误绿。
    timestamps = chain(
        (
            "2026-07-21T00:00:03Z",
            "2026-07-21T00:00:10Z",
            "2026-07-21T00:00:02Z",
            "2026-07-21T00:00:11Z",
            "2026-07-21T00:00:01Z",
            "2026-07-21T00:00:12Z",
            "2026-07-21T00:00:13Z",
            "2026-07-21T00:00:14Z",
            "2026-07-21T00:00:15Z",
            "2026-07-21T00:00:16Z",
            "2026-07-21T00:00:17Z",
            "2026-07-21T00:00:18Z",
        ),
        repeat("2026-07-21T00:00:59Z"),
    )
    monkeypatch.setattr(store_module, "_now_iso", lambda: next(timestamps))
    tasks = create_tasks(store, 3, prefix="created-order")
    expected_ids = [
        task.task_id for task in sorted(tasks, key=lambda task: task.created_at)
    ]
    assert expected_ids != [task.task_id for task in tasks]
    for task in tasks:
        store.promote_to_warm(task.task_id)

    assert [task.task_id for task in store.read_snapshot().warm_tasks()] == expected_ids


def test_warm_rank_overrides_created_order(store: ModelOsStore) -> None:
    tasks = create_tasks(store, 3, prefix="rank-order")
    for task in tasks:
        store.promote_to_warm(task.task_id)
    store.set_warm_rank(tasks[2].task_id, 1)
    store.set_warm_rank(tasks[0].task_id, 2)
    store.set_warm_rank(tasks[1].task_id, 3)

    assert [task.task_id for task in store.read_snapshot().warm_tasks()] == [
        tasks[2].task_id,
        tasks[0].task_id,
        tasks[1].task_id,
    ]


def test_demote_to_backlog_frees_warm_slot(store: ModelOsStore) -> None:
    tasks = create_tasks(store, 4, prefix="demote-capacity")
    for task in tasks[:3]:
        store.promote_to_warm(task.task_id)
    store.demote_to_backlog(tasks[1].task_id)
    store.promote_to_warm(tasks[3].task_id)

    snapshot = store.read_snapshot()
    assert {task.task_id for task in snapshot.warm_tasks()} == {
        tasks[0].task_id,
        tasks[2].task_id,
        tasks[3].task_id,
    }
    demoted, demoted_primary = read_task_bundle(snapshot, tasks[1].task_id)
    assert demoted.status == TaskStatus.BACKLOG
    assert demoted.warm is False
    assert demoted_primary.status == WorkItemStatus.PENDING


def test_demote_foreground_refused(store: ModelOsStore) -> None:
    created = create_tasks(store, 1, prefix="foreground-demote")[0]
    store.promote_to_warm(created.task_id)
    store.claim_foreground(created.task_id)

    with pytest.raises(TaskCommandError, match="release foreground first"):
        store.demote_to_backlog(created.task_id)

    snapshot = store.read_snapshot()
    task, primary = read_task_bundle(snapshot, created.task_id)
    assert task.status == TaskStatus.RUNNING
    assert task.warm is True
    assert primary.status == WorkItemStatus.RUNNING
    assert snapshot.foreground_task_id == created.task_id
