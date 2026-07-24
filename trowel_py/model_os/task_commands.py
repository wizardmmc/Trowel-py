"""Task 生命周期命令的事务编排。"""

from __future__ import annotations

import sqlite3
from contextlib import AbstractContextManager
from collections.abc import Callable
from typing import Any, Protocol

from trowel_py.model_os.reducer import Snapshot, TaskState
from trowel_py.model_os.types import (
    EventEnvelope,
    EventKind,
    MemoryEligibility,
    Provenance,
    SessionPurpose,
    Task,
    TaskOrigin,
    TaskStatus,
    WaitingCondition,
    WorkItemKind,
    WorkItemStatus,
)


class TaskCommandStore(Protocol):
    _conn: sqlite3.Connection | None
    _policy_version: str
    _warm_limit: int

    def _tx(self) -> AbstractContextManager[None]: ...

    def replay(self, from_seq: int = 0) -> Snapshot: ...

    def _task_state_to_task(self, state: TaskState) -> Task: ...

    def _require_task(self, snap: Snapshot, task_id: str) -> TaskState: ...

    def _require_non_terminal(self, task: TaskState) -> None: ...

    def _require_status(self, task: TaskState, allowed: set[TaskStatus]) -> None: ...

    def _read_foreground_task_id(self) -> str | None: ...

    def _insert_event_in_tx(self, event: EventEnvelope) -> int | None: ...

    def _make_task_event(
        self,
        kind: str,
        task_id: str,
        payload: dict[str, Any],
        provenance: Provenance = Provenance.MACHINE_OBSERVATION,
        work_item_id: str | None = None,
    ) -> EventEnvelope: ...

    def _work_item_status_event(
        self,
        work_item_id: str,
        new_status: WorkItemStatus,
        task_id: str | None,
        now: str,
    ) -> EventEnvelope: ...

    def _release_foreground_in_tx(self, task_id: str) -> None: ...

    def _set_waiting(self, task_id: str, waiting: WaitingCondition) -> None: ...

    def _set_waiting_in_tx(
        self,
        task_id: str,
        waiting: WaitingCondition,
        snap: Snapshot | None = None,
    ) -> None: ...


class TaskCommands:
    """通过 Store 的事务原语执行 Task 状态变更。"""

    def __init__(
        self,
        store: TaskCommandStore,
        *,
        now: Callable[[], str],
        new_id: Callable[[], str],
        event_type: Callable[..., EventEnvelope],
        task_error: Callable[[str], Exception],
        warm_full: Callable[[int, tuple[str, ...]], Exception],
        foreground_conflict: Callable[[str | None], Exception],
    ) -> None:
        self._store = store
        self._now = now
        self._new_id = new_id
        self._event_type = event_type
        self._task_error = task_error
        self._warm_full = warm_full
        self._foreground_conflict = foreground_conflict

    def create_task_from_user_request(
        self,
        *,
        original_goal: str,
        idempotency_key: str,
        authorization_scope: str = "",
        priority: int = 0,
    ) -> Task:
        store = self._store
        conn = store._conn
        assert conn is not None
        if not original_goal:
            raise self._task_error("original_goal must be non-empty")
        if not isinstance(idempotency_key, str) or not idempotency_key.strip():
            # NULL 或空白键无法被等值查询命中，会绕过幂等检查并产生重复 Task。
            raise self._task_error("idempotency_key must be a non-empty string")
        with store._tx():
            existing = conn.execute(
                "SELECT task_id FROM task_create_keys WHERE idempotency_key=?",
                (idempotency_key,),
            ).fetchone()
            if existing is not None:
                snap_pre = store.replay()
                return store._task_state_to_task(
                    store._require_task(snap_pre, existing["task_id"])
                )

            task_id = self._new_id()
            work_item_id = self._new_id()
            now = self._now()
            store._insert_event_in_tx(
                self._event_type(
                    event_id=f"wi.create.{work_item_id}",
                    kind=EventKind.WORK_ITEM_CREATED,
                    occurred_at=now,
                    source="kernel",
                    provenance=Provenance.MACHINE_OBSERVATION,
                    policy_version=store._policy_version,
                    payload={
                        "work_item_id": work_item_id,
                        "kind": WorkItemKind.TASK.value,
                        "owner_ref": "user",
                        "task_id": task_id,
                        "status": WorkItemStatus.PENDING.value,
                        "session_purpose": SessionPurpose.FOREGROUND.value,
                        "memory_eligibility": MemoryEligibility.ELIGIBLE.value,
                    },
                    work_item_id=work_item_id,
                    task_id=task_id,
                )
            )
            store._insert_event_in_tx(
                self._event_type(
                    event_id=f"task.create.{task_id}",
                    kind=EventKind.TASK_CREATED,
                    occurred_at=now,
                    source="kernel",
                    provenance=Provenance.USER_DECISION,
                    policy_version=store._policy_version,
                    payload={
                        "task_id": task_id,
                        "origin": TaskOrigin.USER_REQUEST.value,
                        "original_goal": original_goal,
                        "appended_constraints": [],
                        "status": TaskStatus.BACKLOG.value,
                        "priority": priority,
                        "warm": False,
                        "warm_rank": None,
                        "authorization_scope": authorization_scope,
                        "primary_work_item_id": work_item_id,
                    },
                    task_id=task_id,
                )
            )
            conn.execute(
                "INSERT INTO task_create_keys (idempotency_key, task_id, created_at) "
                "VALUES (?, ?, ?)",
                (idempotency_key, task_id, now),
            )
        snap = store.replay()
        return store._task_state_to_task(store._require_task(snap, task_id))

    def promote_to_warm(self, task_id: str) -> None:
        store = self._store
        assert store._conn is not None
        with store._tx():
            snap = store.replay()
            task = store._require_task(snap, task_id)
            store._require_non_terminal(task)
            if task.warm:
                return
            if len(snap.warm_tasks()) >= store._warm_limit:
                raise self._warm_full(
                    store._warm_limit,
                    tuple(item.task_id for item in snap.warm_tasks()),
                )
            now = self._now()
            if task.status == TaskStatus.BACKLOG:
                store._insert_event_in_tx(
                    store._make_task_event(
                        EventKind.TASK_STATUS_CHANGED,
                        task_id,
                        {"new_status": TaskStatus.READY.value},
                    )
                )
                if task.primary_work_item_id:
                    store._insert_event_in_tx(
                        store._work_item_status_event(
                            task.primary_work_item_id,
                            WorkItemStatus.READY,
                            task_id,
                            now,
                        )
                    )
            store._insert_event_in_tx(
                store._make_task_event(
                    EventKind.TASK_WARM_CHANGED, task_id, {"warm": True}
                )
            )

    def demote_to_backlog(self, task_id: str) -> None:
        store = self._store
        assert store._conn is not None
        with store._tx():
            snap = store.replay()
            task = store._require_task(snap, task_id)
            store._require_non_terminal(task)
            if store._read_foreground_task_id() == task_id:
                raise self._task_error(
                    f"cannot demote foreground task {task_id!r}; "
                    "release foreground first"
                )
            now = self._now()
            if task.warm:
                store._insert_event_in_tx(
                    store._make_task_event(
                        EventKind.TASK_WARM_CHANGED, task_id, {"warm": False}
                    )
                )
            if task.status != TaskStatus.BACKLOG:
                store._insert_event_in_tx(
                    store._make_task_event(
                        EventKind.TASK_STATUS_CHANGED,
                        task_id,
                        {"new_status": TaskStatus.BACKLOG.value},
                    )
                )
                if task.primary_work_item_id:
                    store._insert_event_in_tx(
                        store._work_item_status_event(
                            task.primary_work_item_id,
                            WorkItemStatus.PENDING,
                            task_id,
                            now,
                        )
                    )

    def claim_foreground(self, task_id: str) -> None:
        store = self._store
        conn = store._conn
        assert conn is not None
        with store._tx():
            snap = store.replay()
            task = store._require_task(snap, task_id)
            store._require_non_terminal(task)
            if not task.warm:
                raise self._task_error(
                    f"task {task_id!r} must be warm before claiming foreground"
                )
            store._require_status(task, {TaskStatus.READY, TaskStatus.RUNNING})
            current = store._read_foreground_task_id()
            if current == task_id:
                return
            if current is not None:
                raise self._foreground_conflict(current)
            now = self._now()
            cur = conn.execute(
                "UPDATE foreground_claim SET task_id=? WHERE id=1 AND task_id IS NULL",
                (task_id,),
            )
            if cur.rowcount == 0:
                raise self._foreground_conflict(store._read_foreground_task_id())
            store._insert_event_in_tx(
                store._make_task_event(
                    EventKind.TASK_STATUS_CHANGED,
                    task_id,
                    {"new_status": TaskStatus.RUNNING.value},
                )
            )
            if task.primary_work_item_id:
                store._insert_event_in_tx(
                    store._work_item_status_event(
                        task.primary_work_item_id,
                        WorkItemStatus.RUNNING,
                        task_id,
                        now,
                    )
                )
            store._insert_event_in_tx(
                store._make_task_event(
                    EventKind.FOREGROUND_CLAIMED, task_id, {"task_id": task_id}
                )
            )

    def release_foreground(self) -> None:
        store = self._store
        conn = store._conn
        assert conn is not None
        with store._tx():
            current = store._read_foreground_task_id()
            if current is None:
                return
            snap = store.replay()
            now = self._now()
            conn.execute("UPDATE foreground_claim SET task_id=NULL WHERE id=1")
            store._insert_event_in_tx(
                store._make_task_event(EventKind.FOREGROUND_RELEASED, current, {})
            )
            task = next((item for item in snap.tasks if item.task_id == current), None)
            if task is not None and not task.status.is_terminal:
                store._insert_event_in_tx(
                    store._make_task_event(
                        EventKind.TASK_STATUS_CHANGED,
                        current,
                        {"new_status": TaskStatus.READY.value},
                    )
                )
                if task.primary_work_item_id:
                    store._insert_event_in_tx(
                        store._work_item_status_event(
                            task.primary_work_item_id,
                            WorkItemStatus.READY,
                            current,
                            now,
                        )
                    )

    def set_waiting_user(
        self,
        task_id: str,
        *,
        cause: str,
        correlation_id: str,
        deadline: str | None = None,
    ) -> None:
        if not cause:
            raise self._task_error("waiting_user cause must be non-empty")
        if not correlation_id:
            raise self._task_error("waiting_user requires correlation_id")
        self._store._set_waiting(
            task_id,
            WaitingCondition(
                kind=TaskStatus.WAITING_USER.value,
                cause=cause,
                correlation_id=correlation_id,
                deadline=deadline,
            ),
        )

    def set_waiting_event(
        self,
        task_id: str,
        *,
        cause: str,
        condition_kind: str,
        target_ref: str,
        match_params: dict[str, Any] | None = None,
        deadline: str | None = None,
    ) -> None:
        if not cause:
            raise self._task_error("waiting_event cause must be non-empty")
        if not condition_kind or not target_ref:
            raise self._task_error(
                "waiting_event requires condition_kind and target_ref"
            )
        self._store._set_waiting(
            task_id,
            WaitingCondition(
                kind=TaskStatus.WAITING_EVENT.value,
                cause=cause,
                condition_kind=condition_kind,
                target_ref=target_ref,
                match_params=match_params,
                deadline=deadline,
            ),
        )

    def set_incubating(
        self,
        task_id: str,
        *,
        open_question: str,
        preparation_snapshot_ref: str,
        earliest_review_at: str | None = None,
    ) -> None:
        if not open_question or not preparation_snapshot_ref:
            raise self._task_error(
                "incubating requires open_question and preparation_snapshot_ref"
            )
        self._store._set_waiting(
            task_id,
            WaitingCondition(
                kind=TaskStatus.INCUBATING.value,
                cause=open_question,
                open_question=open_question,
                preparation_snapshot_ref=preparation_snapshot_ref,
                earliest_review_at=earliest_review_at,
            ),
        )

    def clear_waiting(self, task_id: str) -> None:
        store = self._store
        assert store._conn is not None
        with store._tx():
            snap = store.replay()
            task = store._require_task(snap, task_id)
            store._require_non_terminal(task)
            if task.status not in (
                TaskStatus.WAITING_USER,
                TaskStatus.WAITING_EVENT,
                TaskStatus.INCUBATING,
            ):
                raise self._task_error(
                    f"task {task_id!r} is not waiting (status={task.status.value})"
                )
            now = self._now()
            if task.primary_work_item_id:
                store._insert_event_in_tx(
                    store._work_item_status_event(
                        task.primary_work_item_id,
                        WorkItemStatus.READY,
                        task_id,
                        now,
                    )
                )
            store._insert_event_in_tx(
                store._make_task_event(EventKind.TASK_WAITING_CLEARED, task_id, {})
            )

    def complete_task(
        self,
        task_id: str,
        *,
        confirmed_by: str,
        evidence_refs: tuple[str, ...] = (),
        confirmation_provenance: Provenance = Provenance.USER_DECISION,
    ) -> None:
        store = self._store
        assert store._conn is not None
        if not confirmed_by:
            raise self._task_error("confirmed_by must be non-empty")
        if not evidence_refs:
            raise self._task_error(
                "evidence_refs must be non-empty (model self-report is not "
                "sufficient — codex review M2)"
            )
        with store._tx():
            snap = store.replay()
            task = store._require_task(snap, task_id)
            store._require_non_terminal(task)
            if (
                task.origin == TaskOrigin.USER_REQUEST
                and confirmation_provenance != Provenance.USER_DECISION
            ):
                raise self._task_error(
                    f"user-requested task {task_id!r} completion requires "
                    f"USER_DECISION (got {confirmation_provenance.value})"
                )
            store._require_status(task, {TaskStatus.RUNNING})
            if store._read_foreground_task_id() == task_id:
                store._release_foreground_in_tx(task_id)
            now = self._now()
            if task.primary_work_item_id:
                store._insert_event_in_tx(
                    store._work_item_status_event(
                        task.primary_work_item_id,
                        WorkItemStatus.DONE,
                        task_id,
                        now,
                    )
                )
            store._insert_event_in_tx(
                store._make_task_event(
                    EventKind.TASK_COMPLETED,
                    task_id,
                    {
                        "confirmed_by": confirmed_by,
                        "confirmation_provenance": confirmation_provenance.value,
                        "evidence_refs": list(evidence_refs),
                    },
                    confirmation_provenance,
                )
            )

    def cancel_task(self, task_id: str, *, reason: str) -> None:
        store = self._store
        assert store._conn is not None
        with store._tx():
            snap = store.replay()
            task = store._require_task(snap, task_id)
            store._require_non_terminal(task)
            if store._read_foreground_task_id() == task_id:
                store._release_foreground_in_tx(task_id)
            now = self._now()
            if task.primary_work_item_id:
                store._insert_event_in_tx(
                    store._work_item_status_event(
                        task.primary_work_item_id,
                        WorkItemStatus.CANCELLED,
                        task_id,
                        now,
                    )
                )
            store._insert_event_in_tx(
                store._make_task_event(
                    EventKind.TASK_CANCELLED,
                    task_id,
                    {"reason": reason},
                )
            )

    def record_task_error(
        self,
        task_id: str,
        *,
        reason: str,
        last_snapshot_ref: str | None = None,
        last_episode_ref: str | None = None,
        recovery_hint: str | None = None,
    ) -> None:
        store = self._store
        assert store._conn is not None
        with store._tx():
            snap = store.replay()
            task = store._require_task(snap, task_id)
            store._require_non_terminal(task)
            if store._read_foreground_task_id() == task_id:
                store._release_foreground_in_tx(task_id)
            now = self._now()
            if task.primary_work_item_id:
                store._insert_event_in_tx(
                    store._work_item_status_event(
                        task.primary_work_item_id,
                        WorkItemStatus.FAILED,
                        task_id,
                        now,
                    )
                )
            store._insert_event_in_tx(
                store._make_task_event(
                    EventKind.TASK_ERROR_RECORDED,
                    task_id,
                    {
                        "origin": task.origin.value,
                        "failure_reason": reason,
                        "last_snapshot_ref": last_snapshot_ref,
                        "last_episode_ref": last_episode_ref,
                        "recovery_hint": recovery_hint,
                    },
                )
            )

    def append_constraint(self, task_id: str, constraint: str) -> None:
        store = self._store
        assert store._conn is not None
        if not constraint:
            raise self._task_error("constraint must be non-empty")
        with store._tx():
            snap = store.replay()
            task = store._require_task(snap, task_id)
            store._require_non_terminal(task)
            store._insert_event_in_tx(
                store._make_task_event(
                    EventKind.TASK_CONSTRAINT_APPENDED,
                    task_id,
                    {"constraint": constraint},
                )
            )

    def set_warm_rank(self, task_id: str, warm_rank: int | None) -> None:
        store = self._store
        assert store._conn is not None
        with store._tx():
            snap = store.replay()
            task = store._require_task(snap, task_id)
            store._require_non_terminal(task)
            store._insert_event_in_tx(
                store._make_task_event(
                    EventKind.TASK_WARM_RANK_SET,
                    task_id,
                    {"warm_rank": warm_rank},
                )
            )

    def change_authorization(
        self,
        task_id: str,
        *,
        authorization_scope: str,
        confirmed_by: str,
    ) -> None:
        store = self._store
        assert store._conn is not None
        if not authorization_scope:
            raise self._task_error("authorization_scope must be non-empty")
        with store._tx():
            snap = store.replay()
            task = store._require_task(snap, task_id)
            store._require_non_terminal(task)
            store._insert_event_in_tx(
                store._make_task_event(
                    EventKind.TASK_AUTHORIZATION_CHANGED,
                    task_id,
                    {
                        "authorization_scope": authorization_scope,
                        "confirmed_by": confirmed_by,
                    },
                    Provenance.USER_DECISION,
                )
            )
