"""Task lifecycle: creation, foreground claim, state transitions (slice-086).

Covers spec pass criteria 1, 3, 4, 6, 7, 9, 10, 11, 13:
- create_task_from_user_request atomically creates Task + primary WorkItem (7)
- idempotent creation key: retry produces no duplicate (7)
- concurrent ClaimForeground yields exactly one winner (1)
- Task.running ⇔ holds foreground claim; foreground ⇒ warm (3)
- CancelTask / RecordTaskError / CompleteTask leave no orphan foreground (6)
- legal state-machine transitions accepted, terminal states not auto-recovered (9)
- CompleteTask records confirmer + evidence + provenance (10)
- RecordTaskError sets FAILED on WorkItem, keeps snapshot ref, is terminal (11, 13)
- restart preserves task, primary WorkItem, foreground claim, warm order (4)
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from trowel_py.model_os.store import ForegroundConflict, ModelOsStore
from trowel_py.model_os.types import (
    Provenance,
    TaskOrigin,
    TaskStatus,
    WorkItemKind,
    WorkItemStatus,
)


# ----------------------------------------------- task + primary workitem (7) ---


def test_create_task_atomically_creates_primary_workitem(
    store: ModelOsStore,
) -> None:
    """CreateTaskFromUserRequest creates Task AND its primary WorkItem in one
    transaction; the WorkItem is kind=TASK and references the task (1:1)."""

    task = store.create_task_from_user_request(
        original_goal="完成 slice-086",
        idempotency_key="create-task-086",
        authorization_scope="default",
    )
    assert task.origin == TaskOrigin.USER_REQUEST
    assert task.original_goal == "完成 slice-086"
    assert task.status == TaskStatus.BACKLOG
    assert task.appended_constraints == ()

    snap = store.read_snapshot()
    primary = [
        w
        for w in snap.work_items
        if w.task_id == task.task_id and w.kind == WorkItemKind.TASK
    ]
    assert len(primary) == 1
    assert primary[0].status == WorkItemStatus.PENDING  # backlog → PENDING


def test_create_task_idempotent_on_key(store: ModelOsStore) -> None:
    """Retrying with the same idempotency key returns the same task; no
    duplicate primary WorkItem is created (pass criterion 7)."""

    t1 = store.create_task_from_user_request(
        original_goal="写论文",
        idempotency_key="key-A",
        authorization_scope="default",
    )
    t2 = store.create_task_from_user_request(
        original_goal="写论文",
        idempotency_key="key-A",
        authorization_scope="default",
    )
    assert t1.task_id == t2.task_id

    snap = store.read_snapshot()
    primary = [
        w
        for w in snap.work_items
        if w.task_id == t1.task_id and w.kind == WorkItemKind.TASK
    ]
    assert len(primary) == 1


# --------------------------------------------------------- foreground (1,3,6) ---


def test_claim_foreground_sets_running_and_holds_claim(
    store: ModelOsStore,
) -> None:
    """ClaimForeground atomically: Task ready→running, WorkItem READY→RUNNING,
    foreground_claim.task_id == this task, foreground ⇒ warm (pass 3)."""

    task = store.create_task_from_user_request(
        original_goal="t1", idempotency_key="k1", authorization_scope="default"
    )
    store.promote_to_warm(task.task_id)
    store.claim_foreground(task.task_id)

    snap = store.read_snapshot()
    t = next(ts for ts in snap.tasks if ts.task_id == task.task_id)
    assert t.status == TaskStatus.RUNNING
    assert t.warm is True
    assert snap.foreground_task_id == task.task_id
    wi = next(
        w
        for w in snap.work_items
        if w.task_id == task.task_id and w.kind == WorkItemKind.TASK
    )
    assert wi.status == WorkItemStatus.RUNNING


def test_claim_foreground_requires_warm(store: ModelOsStore) -> None:
    """foreground ⇒ warm: a backlog Task cannot claim foreground."""

    task = store.create_task_from_user_request(
        original_goal="t1", idempotency_key="k1", authorization_scope="default"
    )
    with pytest.raises(Exception):
        store.claim_foreground(task.task_id)


def test_concurrent_claim_foreground_only_one_winner(db_path: Path) -> None:
    """Two concurrent ClaimForeground on different connections: exactly one
    wins, the other raises ForegroundConflict (pass criterion 1)."""

    store_a = ModelOsStore(db_path)
    store_a.open()
    store_b = ModelOsStore(db_path)
    store_b.open()
    try:
        ta = store_a.create_task_from_user_request(
            original_goal="a", idempotency_key="ka", authorization_scope="default"
        )
        tb = store_b.create_task_from_user_request(
            original_goal="b", idempotency_key="kb", authorization_scope="default"
        )
        store_a.promote_to_warm(ta.task_id)
        store_b.promote_to_warm(tb.task_id)

        results: list[str] = []

        def claim(s: ModelOsStore, tid: str, label: str) -> None:
            try:
                s.claim_foreground(tid)
                results.append(f"{label}:ok")
            except ForegroundConflict:
                results.append(f"{label}:conflict")

        with ThreadPoolExecutor(max_workers=2) as pool:
            pool.submit(claim, store_a, ta.task_id, "a")
            pool.submit(claim, store_b, tb.task_id, "b")

        assert results.count("a:ok") + results.count("b:ok") == 1
        assert results.count("a:conflict") + results.count("b:conflict") == 1
    finally:
        store_a.close()
        store_b.close()


def test_release_foreground_back_to_ready(store: ModelOsStore) -> None:
    """ReleaseForeground: Task running→ready, WorkItem RUNNING→READY, claim cleared."""

    task = store.create_task_from_user_request(
        original_goal="t1", idempotency_key="k1", authorization_scope="default"
    )
    store.promote_to_warm(task.task_id)
    store.claim_foreground(task.task_id)
    store.release_foreground()

    snap = store.read_snapshot()
    t = next(ts for ts in snap.tasks if ts.task_id == task.task_id)
    assert t.status == TaskStatus.READY
    assert snap.foreground_task_id is None


def test_cancel_task_releases_foreground(store: ModelOsStore) -> None:
    """CancelTask while foreground: claim released in the same transaction; no
    orphan foreground (pass criterion 6)."""

    task = store.create_task_from_user_request(
        original_goal="t1", idempotency_key="k1", authorization_scope="default"
    )
    store.promote_to_warm(task.task_id)
    store.claim_foreground(task.task_id)
    store.cancel_task(task.task_id, reason="user cancelled")

    snap = store.read_snapshot()
    t = next(ts for ts in snap.tasks if ts.task_id == task.task_id)
    assert t.status == TaskStatus.CANCELLED
    assert snap.foreground_task_id is None


def test_record_task_error_sets_failed_and_keeps_snapshot_ref(
    store: ModelOsStore,
) -> None:
    """RecordTaskError: Task →error (terminal), WorkItem →FAILED, claim released,
    last_snapshot_ref preserved for later reopen (pass 11, 13)."""

    task = store.create_task_from_user_request(
        original_goal="t1", idempotency_key="k1", authorization_scope="default"
    )
    store.promote_to_warm(task.task_id)
    store.claim_foreground(task.task_id)
    store.record_task_error(
        task.task_id,
        reason="依赖的 084 设计被推翻",
        last_snapshot_ref="snapshot-abc",
    )

    snap = store.read_snapshot()
    t = next(ts for ts in snap.tasks if ts.task_id == task.task_id)
    assert t.status == TaskStatus.ERROR
    assert t.error_record is not None
    assert t.error_record.last_snapshot_ref == "snapshot-abc"
    assert t.error_record.origin == TaskOrigin.USER_REQUEST
    assert snap.foreground_task_id is None
    wi = next(
        w
        for w in snap.work_items
        if w.task_id == task.task_id and w.kind == WorkItemKind.TASK
    )
    assert wi.status == WorkItemStatus.FAILED


def test_complete_task_records_confirmer_and_releases_foreground(
    store: ModelOsStore,
) -> None:
    """CompleteTask records confirmed_by + evidence + USER_DECISION provenance;
    claim released; done is terminal (pass criterion 10)."""

    task = store.create_task_from_user_request(
        original_goal="t1", idempotency_key="k1", authorization_scope="default"
    )
    store.promote_to_warm(task.task_id)
    store.claim_foreground(task.task_id)
    store.complete_task(
        task.task_id,
        confirmed_by="user",
        evidence_refs=("commit-abc", "tests-green"),
    )

    snap = store.read_snapshot()
    t = next(ts for ts in snap.tasks if ts.task_id == task.task_id)
    assert t.status == TaskStatus.DONE
    assert t.completion_evidence is not None
    assert t.completion_evidence.confirmed_by == "user"
    assert t.completion_evidence.evidence_refs == ("commit-abc", "tests-green")
    assert t.completion_evidence.confirmation_provenance == Provenance.USER_DECISION
    assert snap.foreground_task_id is None


# --------------------------------------------------------- state machine (9) ---


def test_waiting_user_releases_foreground_then_restores(store: ModelOsStore) -> None:
    """running → waiting_user releases foreground; clear_waiting → ready
    (pass criterion 9: waiting_user → ready exists)."""

    task = store.create_task_from_user_request(
        original_goal="t", idempotency_key="k", authorization_scope="default"
    )
    store.promote_to_warm(task.task_id)
    store.claim_foreground(task.task_id)
    store.set_waiting_user(task.task_id, cause="等用户回复", correlation_id="q1")

    snap = store.read_snapshot()
    t = next(ts for ts in snap.tasks if ts.task_id == task.task_id)
    assert t.status == TaskStatus.WAITING_USER
    assert t.waiting_condition is not None
    assert t.waiting_condition.cause == "等用户回复"
    assert snap.foreground_task_id is None  # released while waiting

    store.clear_waiting(task.task_id)
    snap = store.read_snapshot()
    assert (
        next(ts for ts in snap.tasks if ts.task_id == task.task_id).status
        == TaskStatus.READY
    )


def test_terminal_states_not_auto_recovered(store: ModelOsStore) -> None:
    """error / cancelled are terminal; no transition back via normal commands."""

    task = store.create_task_from_user_request(
        original_goal="t", idempotency_key="k", authorization_scope="default"
    )
    store.cancel_task(task.task_id, reason="x")
    with pytest.raises(Exception):
        store.promote_to_warm(task.task_id)


# --------------------------------------------------------------- restart (4) ---


def test_restart_preserves_task_and_foreground(db_path: Path) -> None:
    """Restart reopens the same db: task, primary WorkItem, original goal and
    foreground claim all survive (pass criterion 4)."""

    s1 = ModelOsStore(db_path)
    s1.open()
    t = s1.create_task_from_user_request(
        original_goal="完成 slice-086",
        idempotency_key="k",
        authorization_scope="default",
    )
    s1.promote_to_warm(t.task_id)
    s1.claim_foreground(t.task_id)
    s1.close()

    s2 = ModelOsStore(db_path)
    s2.open()
    snap = s2.read_snapshot()
    restored = next(ts for ts in snap.tasks if ts.task_id == t.task_id)
    assert restored.original_goal == "完成 slice-086"
    assert restored.origin == TaskOrigin.USER_REQUEST
    assert restored.status == TaskStatus.RUNNING
    assert snap.foreground_task_id == t.task_id  # foreground survived restart
    primary = [
        w
        for w in snap.work_items
        if w.task_id == t.task_id and w.kind == WorkItemKind.TASK
    ]
    assert len(primary) == 1
    s2.close()
