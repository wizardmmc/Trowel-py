import pytest

from tests.model_os.task_lifecycle.support import read_task_bundle
from trowel_py.model_os.store import ModelOsStore, TaskCommandError
from trowel_py.model_os.types import EventKind, Provenance, TaskStatus, WorkItemStatus


def test_original_goal_immutable_and_constraints_appended(
    store: ModelOsStore,
) -> None:
    created = store.create_task_from_user_request(
        original_goal="原始目标",
        idempotency_key="constraint-task",
        authorization_scope="default",
    )
    store.append_constraint(created.task_id, "约束一")
    store.append_constraint(created.task_id, "约束二")

    task, _ = read_task_bundle(store.read_snapshot(), created.task_id)
    assert task.original_goal == "原始目标"
    assert task.appended_constraints == ("约束一", "约束二")


def test_authorization_change_records_user_decision(
    store: ModelOsStore,
) -> None:
    created = store.create_task_from_user_request(
        original_goal="整理资料",
        idempotency_key="authorization-task",
        authorization_scope="default",
    )
    store.change_authorization(
        created.task_id,
        authorization_scope="elevated",
        confirmed_by="user",
    )

    task, _ = read_task_bundle(store.read_snapshot(), created.task_id)
    assert task.authorization_scope == "elevated"
    authorization_event = next(
        event
        for _, event in store.list_events()
        if event.kind == EventKind.TASK_AUTHORIZATION_CHANGED
    )
    assert authorization_event.provenance == Provenance.USER_DECISION
    assert authorization_event.payload["confirmed_by"] == "user"


def test_authorization_change_on_terminal_rejected(
    store: ModelOsStore,
) -> None:
    created = store.create_task_from_user_request(
        original_goal="整理资料",
        idempotency_key="cancelled-authorization",
        authorization_scope="default",
    )
    store.cancel_task(created.task_id, reason="用户取消")

    with pytest.raises(TaskCommandError, match="terminal"):
        store.change_authorization(
            created.task_id,
            authorization_scope="elevated",
            confirmed_by="user",
        )

    task, primary = read_task_bundle(store.read_snapshot(), created.task_id)
    assert task.status == TaskStatus.CANCELLED
    assert task.authorization_scope == "default"
    assert primary.status == WorkItemStatus.CANCELLED
    assert all(
        event.kind != EventKind.TASK_AUTHORIZATION_CHANGED
        for _, event in store.list_events()
    )
