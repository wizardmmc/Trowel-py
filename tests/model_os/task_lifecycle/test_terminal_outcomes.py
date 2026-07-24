from tests.model_os.task_lifecycle.support import read_task_bundle
from trowel_py.model_os.store import ModelOsStore
from trowel_py.model_os.types import (
    Provenance,
    TaskOrigin,
    TaskStatus,
    WorkItemStatus,
)


def test_cancel_task_releases_foreground(store: ModelOsStore) -> None:
    created = store.create_task_from_user_request(
        original_goal="整理资料",
        idempotency_key="cancel-task",
        authorization_scope="default",
    )
    store.promote_to_warm(created.task_id)
    store.claim_foreground(created.task_id)
    store.cancel_task(created.task_id, reason="用户取消")

    snapshot = store.read_snapshot()
    task, primary = read_task_bundle(snapshot, created.task_id)
    assert task.status == TaskStatus.CANCELLED
    assert primary.status == WorkItemStatus.CANCELLED
    assert snapshot.foreground_task_id is None


def test_record_task_error_sets_failed_and_keeps_snapshot_ref(
    store: ModelOsStore,
) -> None:
    created = store.create_task_from_user_request(
        original_goal="整理资料",
        idempotency_key="failed-task",
        authorization_scope="default",
    )
    store.promote_to_warm(created.task_id)
    store.claim_foreground(created.task_id)
    store.record_task_error(
        created.task_id,
        reason="依赖服务不可用",
        last_snapshot_ref="snapshot-abc",
    )

    snapshot = store.read_snapshot()
    task, primary = read_task_bundle(snapshot, created.task_id)
    assert task.status == TaskStatus.ERROR
    assert task.error_record is not None
    assert task.error_record.last_snapshot_ref == "snapshot-abc"
    assert task.error_record.origin == TaskOrigin.USER_REQUEST
    assert primary.status == WorkItemStatus.FAILED
    assert snapshot.foreground_task_id is None


def test_complete_task_records_confirmer_and_releases_foreground(
    store: ModelOsStore,
) -> None:
    created = store.create_task_from_user_request(
        original_goal="整理资料",
        idempotency_key="complete-task",
        authorization_scope="default",
    )
    store.promote_to_warm(created.task_id)
    store.claim_foreground(created.task_id)
    store.complete_task(
        created.task_id,
        confirmed_by="user",
        evidence_refs=("commit-abc", "tests-green"),
    )

    snapshot = store.read_snapshot()
    task, primary = read_task_bundle(snapshot, created.task_id)
    assert task.status == TaskStatus.DONE
    assert task.completion_evidence is not None
    assert task.completion_evidence.confirmed_by == "user"
    assert task.completion_evidence.evidence_refs == ("commit-abc", "tests-green")
    assert task.completion_evidence.confirmation_provenance == Provenance.USER_DECISION
    assert primary.status == WorkItemStatus.DONE
    assert snapshot.foreground_task_id is None
