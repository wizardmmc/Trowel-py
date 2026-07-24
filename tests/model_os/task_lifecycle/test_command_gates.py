import pytest

from tests.model_os.task_lifecycle.support import read_task_bundle
from trowel_py.model_os.store import ModelOsStore, TaskCommandError
from trowel_py.model_os.types import (
    EventEnvelope,
    EventKind,
    Provenance,
    TaskStatus,
    WorkItemStatus,
)

TASK_LIFECYCLE_KINDS = (
    EventKind.TASK_CREATED,
    EventKind.TASK_STATUS_CHANGED,
    EventKind.TASK_CONSTRAINT_APPENDED,
    EventKind.TASK_WARM_CHANGED,
    EventKind.TASK_WARM_RANK_SET,
    EventKind.TASK_WAITING_SET,
    EventKind.TASK_WAITING_CLEARED,
    EventKind.TASK_AUTHORIZATION_CHANGED,
    EventKind.TASK_COMPLETED,
    EventKind.TASK_CANCELLED,
    EventKind.TASK_ERROR_RECORDED,
    EventKind.FOREGROUND_CLAIMED,
    EventKind.FOREGROUND_RELEASED,
)


def test_user_task_done_rejects_model_self_report(store: ModelOsStore) -> None:
    created = store.create_task_from_user_request(
        original_goal="整理资料",
        idempotency_key="model-completion",
        authorization_scope="default",
    )
    store.promote_to_warm(created.task_id)
    store.claim_foreground(created.task_id)

    with pytest.raises(TaskCommandError, match="USER_DECISION"):
        store.complete_task(
            created.task_id,
            confirmed_by="model",
            evidence_refs=("model-report",),
            confirmation_provenance=Provenance.MODEL_HYPOTHESIS,
        )

    snapshot = store.read_snapshot()
    task, primary = read_task_bundle(snapshot, created.task_id)
    assert task.status == TaskStatus.RUNNING
    assert primary.status == WorkItemStatus.RUNNING
    assert snapshot.foreground_task_id == created.task_id


@pytest.mark.parametrize("terminal", ["cancelled", "error", "done"])
def test_terminal_task_rejects_promote(
    store: ModelOsStore,
    terminal: str,
) -> None:
    # 终态不能被普通生命周期命令隐式复活。
    created = store.create_task_from_user_request(
        original_goal="整理资料",
        idempotency_key=f"terminal-{terminal}",
        authorization_scope="default",
    )
    if terminal == "cancelled":
        store.cancel_task(created.task_id, reason="用户取消")
        expected_task = TaskStatus.CANCELLED
        expected_work_item = WorkItemStatus.CANCELLED
    elif terminal == "error":
        store.record_task_error(created.task_id, reason="依赖服务不可用")
        expected_task = TaskStatus.ERROR
        expected_work_item = WorkItemStatus.FAILED
    elif terminal == "done":
        store.promote_to_warm(created.task_id)
        store.claim_foreground(created.task_id)
        store.complete_task(
            created.task_id,
            confirmed_by="user",
            evidence_refs=("artifact",),
        )
        expected_task = TaskStatus.DONE
        expected_work_item = WorkItemStatus.DONE
    else:
        raise AssertionError(f"unknown terminal fixture: {terminal}")

    with pytest.raises(TaskCommandError, match="terminal"):
        store.promote_to_warm(created.task_id)

    snapshot = store.read_snapshot()
    task, primary = read_task_bundle(snapshot, created.task_id)
    assert task.status == expected_task
    assert task.warm is False
    assert primary.status == expected_work_item
    assert snapshot.foreground_task_id is None


def test_append_event_rejects_task_lifecycle_kinds(
    store: ModelOsStore,
) -> None:
    for kind in TASK_LIFECYCLE_KINDS:
        forged = EventEnvelope(
            event_id=f"forged-{kind}",
            kind=kind,
            occurred_at="2026-07-21T00:00:00Z",
            source="untrusted-caller",
            provenance=Provenance.MODEL_HYPOTHESIS,
            policy_version="v0",
            payload={},
            task_id="unknown-task",
        )
        with pytest.raises(TaskCommandError, match="structured command"):
            store.append_event(forged)

    assert store.list_events() == []


def test_claim_foreground_rejects_waiting_state(
    store: ModelOsStore,
) -> None:
    created = store.create_task_from_user_request(
        original_goal="整理资料",
        idempotency_key="waiting-claim",
        authorization_scope="default",
    )
    store.promote_to_warm(created.task_id)
    store.claim_foreground(created.task_id)
    store.set_waiting_user(
        created.task_id,
        cause="等待补充信息",
        correlation_id="question-1",
    )

    with pytest.raises(TaskCommandError, match="allowed source states"):
        store.claim_foreground(created.task_id)

    snapshot = store.read_snapshot()
    task, primary = read_task_bundle(snapshot, created.task_id)
    assert task.status == TaskStatus.WAITING_USER
    assert task.warm is True
    assert primary.status == WorkItemStatus.SUSPENDED
    assert snapshot.foreground_task_id is None


def test_complete_rejects_non_running_state(store: ModelOsStore) -> None:
    created = store.create_task_from_user_request(
        original_goal="整理资料",
        idempotency_key="ready-completion",
        authorization_scope="default",
    )
    store.promote_to_warm(created.task_id)

    with pytest.raises(TaskCommandError, match="allowed source states"):
        store.complete_task(
            created.task_id,
            confirmed_by="user",
            evidence_refs=("artifact",),
        )

    snapshot = store.read_snapshot()
    task, primary = read_task_bundle(snapshot, created.task_id)
    assert task.status == TaskStatus.READY
    assert primary.status == WorkItemStatus.READY
    assert snapshot.foreground_task_id is None


def test_complete_rejects_empty_evidence(store: ModelOsStore) -> None:
    created = store.create_task_from_user_request(
        original_goal="整理资料",
        idempotency_key="empty-evidence",
        authorization_scope="default",
    )
    store.promote_to_warm(created.task_id)
    store.claim_foreground(created.task_id)

    with pytest.raises(TaskCommandError, match="evidence_refs"):
        store.complete_task(
            created.task_id,
            confirmed_by="user",
            evidence_refs=(),
        )

    snapshot = store.read_snapshot()
    task, primary = read_task_bundle(snapshot, created.task_id)
    assert task.status == TaskStatus.RUNNING
    assert primary.status == WorkItemStatus.RUNNING
    assert snapshot.foreground_task_id == created.task_id
