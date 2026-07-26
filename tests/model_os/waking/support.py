from __future__ import annotations

from trowel_py.model_os.store import ModelOsStore


def running_task(store: ModelOsStore, key: str = "wake-task"):
    task = store.create_task_from_user_request(
        original_goal="等待外部条件",
        idempotency_key=key,
        authorization_scope="default",
    )
    store.promote_to_warm(task.task_id)
    store.claim_foreground(task.task_id)
    return task
