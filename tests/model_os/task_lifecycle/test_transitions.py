from tests.model_os.task_lifecycle.support import read_task_bundle
from trowel_py.model_os.store import ModelOsStore
from trowel_py.model_os.types import TaskStatus, WorkItemStatus


def test_release_foreground_back_to_ready(store: ModelOsStore) -> None:
    created = store.create_task_from_user_request(
        original_goal="整理资料",
        idempotency_key="release-task",
        authorization_scope="default",
    )
    store.promote_to_warm(created.task_id)
    store.claim_foreground(created.task_id)
    store.release_foreground()

    snapshot = store.read_snapshot()
    task, primary = read_task_bundle(snapshot, created.task_id)
    assert task.status == TaskStatus.READY
    assert primary.status == WorkItemStatus.READY
    assert snapshot.foreground_task_id is None


def test_waiting_user_releases_foreground_then_restores(
    store: ModelOsStore,
) -> None:
    created = store.create_task_from_user_request(
        original_goal="整理资料",
        idempotency_key="waiting-task",
        authorization_scope="default",
    )
    store.promote_to_warm(created.task_id)
    store.claim_foreground(created.task_id)
    store.set_waiting_user(
        created.task_id,
        cause="等待补充信息",
        correlation_id="question-1",
    )

    waiting_snapshot = store.read_snapshot()
    waiting_task, waiting_primary = read_task_bundle(
        waiting_snapshot,
        created.task_id,
    )
    assert waiting_task.status == TaskStatus.WAITING_USER
    assert waiting_task.waiting_condition is not None
    assert waiting_task.waiting_condition.cause == "等待补充信息"
    assert waiting_primary.status == WorkItemStatus.SUSPENDED
    assert waiting_snapshot.foreground_task_id is None

    store.clear_waiting(created.task_id)
    ready_snapshot = store.read_snapshot()
    ready_task, ready_primary = read_task_bundle(ready_snapshot, created.task_id)
    assert ready_task.status == TaskStatus.READY
    assert ready_task.waiting_condition is None
    assert ready_primary.status == WorkItemStatus.READY
