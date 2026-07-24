from __future__ import annotations

from pathlib import Path

import pytest

from trowel_py.model_os.store import ModelOsStore
from trowel_py.model_os.types import (
    MemoryEligibility,
    SessionPurpose,
    WorkItemKind,
    WorkItemStatus,
)


def test_open_bootstraps_missing_db(db_path: Path) -> None:
    assert not db_path.exists()
    store = ModelOsStore(db_path)
    store.open()
    assert db_path.exists()

    tables = {
        row["name"]
        for row in store._conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    # work_items 由事件归约生成，因此不单独维护可变表。
    for required in ("events", "decisions", "leases", "meta"):
        assert required in tables, f"missing table {required}"

    snap = store.read_snapshot()
    assert snap.schema_version >= 1


def test_open_idempotent_on_existing_db(store: ModelOsStore) -> None:
    created = store.create_task_from_user_request(
        original_goal="t", idempotency_key="k", authorization_scope="d"
    )
    store.close()

    reopened = ModelOsStore(store.path)
    reopened.open()
    snap = reopened.read_snapshot()
    assert any(
        wi.work_item_id == created.primary_work_item_id for wi in snap.work_items
    )


@pytest.mark.parametrize(
    "kind,owner,task_id,purpose,eligibility",
    [
        (
            WorkItemKind.DEFAULT,
            "system",
            None,
            SessionPurpose.DEFAULT,
            MemoryEligibility.INELIGIBLE,
        ),
        (
            WorkItemKind.INCUBATION,
            "system",
            "task-A",
            SessionPurpose.INCUBATION,
            MemoryEligibility.INELIGIBLE,
        ),
        (
            WorkItemKind.MAINTENANCE,
            "system",
            None,
            SessionPurpose.MAINTENANCE,
            MemoryEligibility.INELIGIBLE,
        ),
        (
            WorkItemKind.EXPERIMENT,
            "system",
            None,
            SessionPurpose.EXPERIMENT,
            MemoryEligibility.INELIGIBLE,
        ),
    ],
)
def test_all_work_item_kinds_are_legal(
    store: ModelOsStore,
    kind: WorkItemKind,
    owner: str,
    task_id: str | None,
    purpose: SessionPurpose,
    eligibility: MemoryEligibility,
) -> None:
    wi = store.create_work_item(
        kind=kind,
        owner_ref=owner,
        task_id=task_id,
        session_purpose=purpose,
        memory_eligibility=eligibility,
    )
    assert wi.kind == kind
    assert wi.task_id == task_id
    assert wi.session_purpose == purpose
    assert wi.memory_eligibility == eligibility
    assert wi.status == WorkItemStatus.PENDING

    snap = store.read_snapshot()
    assert any(w.work_item_id == wi.work_item_id for w in snap.work_items)


def test_system_work_excluded_from_task_set(store: ModelOsStore) -> None:
    task = store.create_task_from_user_request(
        original_goal="t", idempotency_key="k", authorization_scope="d"
    )
    store.create_work_item(
        kind=WorkItemKind.DEFAULT,
        owner_ref="system",
        task_id=None,
        session_purpose=SessionPurpose.DEFAULT,
        memory_eligibility=MemoryEligibility.INELIGIBLE,
    )
    store.create_work_item(
        kind=WorkItemKind.MAINTENANCE,
        owner_ref="system",
        task_id=None,
        session_purpose=SessionPurpose.MAINTENANCE,
        memory_eligibility=MemoryEligibility.INELIGIBLE,
    )

    snap = store.read_snapshot()
    tasks = snap.task_work_items()
    assert all(w.kind == WorkItemKind.TASK for w in tasks)
    assert len(tasks) == 1
    assert tasks[0].task_id == task.task_id


def test_create_work_item_rejects_task_kind(store: ModelOsStore) -> None:
    from trowel_py.model_os.store import TaskCommandError

    with pytest.raises(TaskCommandError):
        store.create_work_item(
            kind=WorkItemKind.TASK,
            owner_ref="user",
            task_id="task-A",
            session_purpose=SessionPurpose.FOREGROUND,
            memory_eligibility=MemoryEligibility.ELIGIBLE,
        )
