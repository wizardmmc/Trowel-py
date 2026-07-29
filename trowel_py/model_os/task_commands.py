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
    """声明 ``TaskCommands`` 所需的 Store 事务、查询和事件原语。"""

    _conn: sqlite3.Connection | None
    _policy_version: str
    _warm_limit: int

    def _tx(self) -> AbstractContextManager[None]:
        """返回 Task 写入使用的事务上下文。"""

        ...

    def replay(self, from_seq: int = 0) -> Snapshot:
        """从 journal 起点返回当前派生快照；兼容参数 ``from_seq`` 只接受 0。"""

        ...

    def _task_state_to_task(self, state: TaskState) -> Task:
        """把内部 TaskState 转换为公开 Task。"""

        ...

    def _require_task(self, snap: Snapshot, task_id: str) -> TaskState:
        """从快照读取 Task，不存在时拒绝命令。"""

        ...

    def _require_non_terminal(self, task: TaskState) -> None:
        """拒绝对终态 Task 执行后续命令。"""

        ...

    def _require_status(self, task: TaskState, allowed: set[TaskStatus]) -> None:
        """要求 Task 处于命令允许的来源状态。"""

        ...

    def _read_foreground_task_id(self) -> str | None:
        """读取当前 foreground Task。"""

        ...

    def _insert_event_in_tx(self, event: EventEnvelope) -> int | None:
        """追加事件；新事件返回序号，完全相同的重复事件返回 None，ID 冲突时拒绝。"""

        ...

    def _make_task_event(
        self,
        kind: str,
        task_id: str,
        payload: dict[str, Any],
        provenance: Provenance = Provenance.MACHINE_OBSERVATION,
        work_item_id: str | None = None,
    ) -> EventEnvelope:
        """构造由内核产生的 Task 事件。"""

        ...

    def _work_item_status_event(
        self,
        work_item_id: str,
        new_status: WorkItemStatus,
        task_id: str | None,
        now: str,
    ) -> EventEnvelope:
        """构造同步主 WorkItem 状态的事件。"""

        ...

    def _release_foreground_in_tx(self, task_id: str) -> None:
        """仅当指定 Task 当前持有 foreground 时才释放，并写入审计事件。"""

        ...

    def _set_waiting(self, task_id: str, waiting: WaitingCondition) -> None:
        """在独立事务中把 RUNNING Task 置为等待态并暂停主 WorkItem。

        Task 持有 foreground 时一并释放。
        """

        ...

    def _set_waiting_in_tx(
        self,
        task_id: str,
        waiting: WaitingCondition,
        snap: Snapshot | None = None,
    ) -> None:
        """在当前事务中执行等待转换。

        Args:
            task_id: 必须处于 RUNNING 的 Task ID。
            waiting: 要写入的等待状态和唤醒条件。
            snap: 已在当前事务中回放的快照；为 None 时重新回放。
        """

        ...


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
        """绑定命令执行所需的 Store、工厂和领域异常构造器。

        Args:
            store: 提供事务、状态查询和事件写入原语的 Store。
            now: 返回事件时间的时钟函数。
            new_id: 返回新 Task 和 WorkItem ID 的工厂。
            event_type: 构造事件 envelope 的工厂。
            task_error: 构造 Task 命令异常的函数。
            warm_full: 构造 warm 容量异常的函数。
            foreground_conflict: 构造 foreground 冲突异常的函数。
        """

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
        """创建用户 Task 及其主 WorkItem。

        ``idempotency_key`` 命中已有请求时直接返回首次创建的 Task，不比较本次
        ``original_goal``、``authorization_scope`` 或 ``priority``。

        Args:
            original_goal: 用户最初提出且后续不可覆盖的目标。
            idempotency_key: 用户请求的非空幂等键。
            authorization_scope: Task 初始授权范围。
            priority: Task 初始调度优先级。

        Returns:
            新建或幂等命中的 Task。
        """

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
        """把非终态 Task 加入有容量上限的 warm 集合。

        BACKLOG Task 同时转为 READY，已经 warm 时幂等返回。
        """

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
        """把非终态 Task 移出 warm 集合并退回 BACKLOG。

        foreground Task 必须先释放；已处于 BACKLOG 且不在 warm 时不写事件。
        """

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
        """让 READY 或 RUNNING 的 warm Task 独占 foreground。

        同一 Task 重试时幂等返回，其他 Task 已占用时拒绝。
        """

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
        """释放 foreground，并把非终态 Task 及主 WorkItem 恢复为 READY。

        当前没有 foreground 时幂等返回。
        """

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
        """把 RUNNING Task 置为 WAITING_USER，并暂停主 WorkItem。

        Task 保持 warm 并继续占用容量；其 foreground 会被释放。

        Args:
            task_id: 要进入等待态的 RUNNING Task ID。
            cause: 本次等待的非空原因。
            correlation_id: 关联后续用户回复的非空 ID。
            deadline: 可选截止时间；为 None 时不设置。
        """

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
        """把 RUNNING Task 置为 WAITING_EVENT，并暂停主 WorkItem。

        Task 保持 warm 并继续占用容量；其 foreground 会被释放。

        Args:
            task_id: 要进入等待态的 RUNNING Task ID。
            cause: 本次等待的非空原因。
            condition_kind: 待观察条件的非空类型。
            target_ref: 待观察对象的非空引用。
            match_params: 可选匹配参数；为 None 时不附加参数。
            deadline: 可选截止时间；为 None 时不设置。
        """

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
        """把 RUNNING Task 连同准备信息置为 INCUBATING，并暂停主 WorkItem。

        Task 保持 warm 并继续占用容量；其 foreground 会被释放。

        Args:
            task_id: 要进入 incubation 的 RUNNING Task ID。
            open_question: incubation 要继续处理的非空问题。
            preparation_snapshot_ref: 进入 incubation 前的非空快照引用。
            earliest_review_at: 可选最早复查时间；为 None 时不设置。
        """

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
        """清除等待条件，把 Task 和主 WorkItem 恢复为 READY。

        保留 warm 状态，且不重新占用 foreground。
        """

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
        """用至少一个证据引用完成 RUNNING Task，并同步结束主 WorkItem。

        完成会清除 warm 和等待状态；USER_REQUEST Task 只接受 USER_DECISION 来源。
        若 Task 持有 foreground，则在同一事务内释放。

        Args:
            task_id: 要完成的 RUNNING Task ID。
            confirmed_by: 非空确认者标识。
            evidence_refs: 至少包含一个完成证据引用。
            confirmation_provenance: 写入完成证据和事件的确认来源。
        """

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
        """取消非终态 Task，清除 warm 和等待状态。

        同步取消主 WorkItem，并释放 Task 持有的 foreground。
        """

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
        """把非终态 Task 结束为 ERROR，并记录恢复线索。

        清除 warm 和等待状态，同步失败主 WorkItem，并释放 Task 持有的 foreground。

        Args:
            task_id: 要结束为 ERROR 的非终态 Task ID。
            reason: 记录到错误事实中的失败原因。
            last_snapshot_ref: 最后可用快照引用；为 None 时不记录。
            last_episode_ref: 最后相关 Episode 引用；为 None 时不记录。
            recovery_hint: 可选恢复建议；为 None 时不记录。
        """

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
        """向非终态 Task 追加非空约束，不覆盖原始目标。"""

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
        """设置非终态 Task 的人工 warm 顺序。

        ``None`` 清除人工顺序；Task 不在 warm 集合中时仍保存该值，供之后进入 warm
        使用。
        """

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
        """记录非终态 Task 的授权范围变更。

        调用方负责在进入此命令前确认用户决定；事件来源固定为 USER_DECISION，
        ``confirmed_by`` 仅作为审计字段写入。
        """

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
