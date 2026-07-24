from trowel_py.model_os.reducer import Snapshot, TaskState, WorkItemState
from trowel_py.model_os.store import ModelOsStore
from trowel_py.model_os.types import Task, WorkItemKind


def read_task_bundle(
    snapshot: Snapshot,
    task_id: str,
) -> tuple[TaskState, WorkItemState]:
    task = next(task for task in snapshot.tasks if task.task_id == task_id)
    primary = [
        item
        for item in snapshot.work_items
        if item.task_id == task_id and item.kind == WorkItemKind.TASK
    ]
    if len(primary) != 1:
        raise AssertionError(
            f"expected one primary WorkItem for {task_id}, got {len(primary)}"
        )
    return task, primary[0]


def create_tasks(
    store: ModelOsStore,
    count: int,
    *,
    prefix: str,
) -> tuple[Task, ...]:
    return tuple(
        store.create_task_from_user_request(
            original_goal=f"任务 {index}",
            idempotency_key=f"{prefix}-{index}",
            authorization_scope="default",
        )
        for index in range(count)
    )
