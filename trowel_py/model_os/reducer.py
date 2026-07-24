"""Model OS journal 的纯 reducer。

事件以确定性规则派生 ``Snapshot``；决策当前只保留在审计日志，不派生状态。
reducer 无 I/O 且不修改输入，相同输入总会得到相同输出。

WorkItem 的弱来源状态不能覆盖强来源状态；未知事件类型只记入
``unrecognized_event_kinds``，不阻断旧版本回放。

``last_seq`` 与 ``last_decision_seq`` 是回放位置，由 store 按真实 SQLite
序号写入，reducer 不推进它们。
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any

from trowel_py.model_os.types import (
    CompletionEvidence,
    EpisodeStatus,
    ErrorRecord,
    EventEnvelope,
    EventKind,
    MemoryEligibility,
    PendingDescriptor,
    Provenance,
    ReconcileReason,
    SessionPurpose,
    SnapshotRef,
    TaskOrigin,
    TaskStatus,
    WaitingCondition,
    WaitingSubtype,
    WorkItemKind,
    WorkItemStatus,
)
from trowel_py.model_os.context_observer import (
    ContextSample,
    context_sample_from_dict,
)
from trowel_py.model_os.context_fold import (
    ContextFoldRuntime,
    apply_context_sample as _run_apply_context_sample,
)
from trowel_py.model_os.episode_fold import (
    EpisodeFoldRuntime,
    _apply_episode_checkpoint as _run_apply_episode_checkpoint,
    _apply_episode_reconcile_required as _run_apply_episode_reconcile_required,
    _apply_episode_reconcile_resolved as _run_apply_episode_reconcile_resolved,
    _apply_episode_status_change as _run_apply_episode_status_change,
    _apply_episode_suspended as _run_apply_episode_suspended,
    _apply_episode_wait_resolved as _run_apply_episode_wait_resolved,
    _find_episode as _run_find_episode,
    _pending_from_payload as _run_pending_from_payload,
    _replace_episode as _run_replace_episode,
    episode_from_created as _run_episode_from_created,
)
from trowel_py.model_os.task_fold import (
    _apply_task_authorization_changed,
    _apply_task_cancelled,
    _apply_task_completed,
    _apply_task_constraint_appended,
    _apply_task_error_recorded,
    _apply_task_status_change,
    _apply_task_waiting_cleared,
    _apply_task_waiting_set,
    _apply_task_warm_changed,
    _apply_task_warm_rank_set,
    _find_task as _find_task,
    _replace_task as _replace_task,
    _waiting_from_payload as _waiting_from_payload,
    task_from_created as _run_task_from_created,
)
from trowel_py.model_os.work_item_fold import (
    WorkItemFoldRuntime,
    _apply_status_change as _run_apply_status_change,
    _replace_work_item as _run_replace_work_item,
    _work_item_from_created as _run_work_item_from_created,
)

_SCHEMA_VERSION = 1
_MAX_DESCRIPTION_LEN = 512


@dataclass(frozen=True)
class WorkItemState:
    """WorkItem 的派生状态。

    ``status_provenance`` 记录当前状态的来源强度，供下一次状态更新执行来源门禁。
    """

    work_item_id: str
    kind: WorkItemKind
    owner_ref: str
    task_id: str | None
    status: WorkItemStatus
    status_provenance: Provenance
    session_purpose: SessionPurpose
    memory_eligibility: MemoryEligibility


@dataclass(frozen=True)
class TaskState:
    """Task 的派生状态。

    Task 不使用 WorkItem 的来源强度门禁，否则用户创建的 Task 将无法接受后续机器状态。
    Task 转移权限由结构化命令入口约束，``status_provenance`` 只用于审计。
    """

    task_id: str
    origin: TaskOrigin
    original_goal: str
    appended_constraints: tuple[str, ...]
    status: TaskStatus
    status_provenance: Provenance
    priority: int
    warm: bool
    warm_rank: int | None
    authorization_scope: str
    waiting_condition: WaitingCondition | None
    completion_evidence: CompletionEvidence | None
    error_record: ErrorRecord | None
    primary_work_item_id: str | None
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class EpisodeState:
    """Episode 的派生状态。

    实时 ownership lease 来自独立表，不由 journal 归约。reducer 只保存
    ``last_snapshot_ref``；快照正文按 ``(episode_id, version)`` 从独立表读取。
    """

    episode_id: str
    work_item_id: str
    task_id: str | None
    status: EpisodeStatus
    status_provenance: Provenance
    native_session_id: str | None
    pending_descriptor: PendingDescriptor | None
    reconcile_reason: ReconcileReason | None
    last_snapshot_ref: SnapshotRef | None
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class UnknownAction:
    """结果尚未确认的动作。

    ``requires_reconcile`` 需要先核对现实，``requires_user_restart`` 要求人为
    重启；journal 回放均不自动重发动作。
    """

    event_id: str
    work_item_id: str | None
    description: str
    reconcile_kind: str


@dataclass(frozen=True)
class ContextObservationState:
    """一个 ``(episode, native_session)`` 的最新上下文观测。

    不可用样本也会覆盖旧的可信数值，避免把缺失信号隐藏起来。``observed_at``
    以事件 envelope 的 ``occurred_at`` 为准，因为 ``ContextSample`` 不带时间。

    session 内的最新值是精确的；跨 session 按时间选择可能命中旧 relay 的晚到样本。
    需要当前 relay 时，调用方必须按 ``native_session_id`` 查询。
    """

    episode_id: str | None
    native_session_id: str
    generation: int
    latest_sample: ContextSample
    observed_at: str


@dataclass(frozen=True)
class Snapshot:
    """journal 与实时表共同组成的派生状态。

    WorkItem、Task、Episode、未知动作和上下文观测可从 journal 重建。
    ``active_leases`` 与 ``foreground_task_id`` 由 store 在读取时从实时表填充；
    对应的 ownership 与 foreground 事件只保留审计轨迹。
    """

    schema_version: int
    last_seq: int
    last_decision_seq: int
    work_items: tuple[WorkItemState, ...]
    tasks: tuple[TaskState, ...]
    episodes: tuple[EpisodeState, ...]
    active_leases: tuple[Any, ...]
    foreground_task_id: str | None
    unknown_actions: tuple[UnknownAction, ...]
    unrecognized_event_kinds: tuple[str, ...]
    context_observations: tuple[ContextObservationState, ...]

    def task_work_items(self) -> tuple[WorkItemState, ...]:
        return tuple(w for w in self.work_items if w.kind == WorkItemKind.TASK)

    def warm_tasks(self) -> tuple[TaskState, ...]:
        """返回未终止的 warm Task。

        排序键先取 ``warm_rank``，未设置时使用 ``10**9``，再按创建时间排序。
        """

        warm = tuple(t for t in self.tasks if t.warm and not t.status.is_terminal)
        return tuple(
            sorted(
                warm,
                key=lambda t: (
                    t.warm_rank if t.warm_rank is not None else 10**9,
                    t.created_at,
                ),
            )
        )

    def episode_by_id(self, episode_id: str | None) -> EpisodeState | None:
        if episode_id is None:
            return None
        return next((e for e in self.episodes if e.episode_id == episode_id), None)

    def non_terminal_episodes_for_work_item(
        self, work_item_id: str
    ) -> tuple[EpisodeState, ...]:
        return tuple(
            e
            for e in self.episodes
            if e.work_item_id == work_item_id and not e.status.is_terminal
        )

    def context_observation(
        self, episode_id: str | None, native_session_id: str
    ) -> ContextObservationState | None:
        return next(
            (
                s
                for s in self.context_observations
                if s.episode_id == episode_id
                and s.native_session_id == native_session_id
            ),
            None,
        )

    def latest_context_sample(
        self, episode_id: str | None
    ) -> ContextObservationState | None:
        """按 ``observed_at`` 返回 Episode 跨 session 的最新观测。

        该结果不保证来自当前 relay；需要当前 session 时应调用
        :meth:`context_observation`。
        """

        candidates = tuple(
            s for s in self.context_observations if s.episode_id == episode_id
        )
        if not candidates:
            return None
        return max(candidates, key=lambda s: s.observed_at)


def initial_snapshot(schema_version: int = _SCHEMA_VERSION) -> Snapshot:
    return Snapshot(
        schema_version=schema_version,
        last_seq=0,
        last_decision_seq=0,
        work_items=(),
        tasks=(),
        episodes=(),
        active_leases=(),
        foreground_task_id=None,
        unknown_actions=(),
        unrecognized_event_kinds=(),
        context_observations=(),
    )


def _work_item_from_created(event: EventEnvelope) -> WorkItemState:
    return _run_work_item_from_created(
        event,
        work_item_state_factory=WorkItemState,
        work_item_kind=WorkItemKind,
        work_item_status=WorkItemStatus,
        provenance=Provenance,
        session_purpose=SessionPurpose,
        memory_eligibility=MemoryEligibility,
    )


def _replace_work_item(
    snap: Snapshot, work_item_id: str, new_state: WorkItemState
) -> Snapshot:
    return _run_replace_work_item(
        snap,
        work_item_id,
        new_state,
        snapshot_replace=replace,
    )


def _work_item_fold_runtime() -> WorkItemFoldRuntime:
    return WorkItemFoldRuntime(
        replace_work_item=_replace_work_item,
        provenance=Provenance,
        work_item_status=WorkItemStatus,
        state_replace=replace,
    )


def _apply_status_change(snap: Snapshot, event: EventEnvelope) -> Snapshot:
    return _run_apply_status_change(
        snap,
        event,
        runtime=_work_item_fold_runtime(),
    )


def _add_unknown_action(snap: Snapshot, event: EventEnvelope, kind: str) -> Snapshot:
    """记录待核对动作；描述会截断，完整 payload 仍保留在 journal。"""

    description = str(event.payload.get("description", ""))[:_MAX_DESCRIPTION_LEN]
    action = UnknownAction(
        event_id=event.event_id,
        work_item_id=event.work_item_id,
        description=description,
        reconcile_kind=kind,
    )
    return replace(snap, unknown_actions=snap.unknown_actions + (action,))


def _task_from_created(event: EventEnvelope) -> TaskState:
    return _run_task_from_created(
        event,
        task_state_factory=TaskState,
    )


def _episode_from_created(event: EventEnvelope) -> EpisodeState:
    return _run_episode_from_created(
        event,
        episode_state_factory=EpisodeState,
        episode_status=EpisodeStatus,
    )


def _find_episode(snap: Snapshot, episode_id: str | None) -> EpisodeState | None:
    return _run_find_episode(snap, episode_id)


def _replace_episode(
    snap: Snapshot, episode_id: str | None, new_state: EpisodeState
) -> Snapshot:
    return _run_replace_episode(
        snap,
        episode_id,
        new_state,
        snapshot_replace=replace,
    )


def _pending_from_payload(p: dict[str, Any]) -> PendingDescriptor:
    return _run_pending_from_payload(
        p,
        pending_descriptor_factory=PendingDescriptor,
        waiting_subtype=WaitingSubtype,
    )


def _episode_fold_runtime() -> EpisodeFoldRuntime:
    return EpisodeFoldRuntime(
        find_episode=_find_episode,
        replace_episode=_replace_episode,
        pending_from_payload=_pending_from_payload,
        episode_status=EpisodeStatus,
        reconcile_reason=ReconcileReason,
        snapshot_ref=SnapshotRef,
        state_replace=replace,
    )


def _apply_episode_status_change(snap: Snapshot, event: EventEnvelope) -> Snapshot:
    return _run_apply_episode_status_change(
        snap, event, runtime=_episode_fold_runtime()
    )


def _apply_episode_checkpoint(snap: Snapshot, event: EventEnvelope) -> Snapshot:
    return _run_apply_episode_checkpoint(snap, event, runtime=_episode_fold_runtime())


def _apply_episode_suspended(snap: Snapshot, event: EventEnvelope) -> Snapshot:
    return _run_apply_episode_suspended(snap, event, runtime=_episode_fold_runtime())


def _apply_episode_wait_resolved(snap: Snapshot, event: EventEnvelope) -> Snapshot:
    return _run_apply_episode_wait_resolved(
        snap, event, runtime=_episode_fold_runtime()
    )


def _apply_episode_reconcile_required(snap: Snapshot, event: EventEnvelope) -> Snapshot:
    return _run_apply_episode_reconcile_required(
        snap, event, runtime=_episode_fold_runtime()
    )


def _apply_episode_reconcile_resolved(snap: Snapshot, event: EventEnvelope) -> Snapshot:
    return _run_apply_episode_reconcile_resolved(
        snap, event, runtime=_episode_fold_runtime()
    )


def _apply_context_sample(snap: Snapshot, event: EventEnvelope) -> Snapshot:
    return _run_apply_context_sample(
        snap,
        event,
        runtime=ContextFoldRuntime(
            decode_sample=context_sample_from_dict,
            context_state_factory=ContextObservationState,
            snapshot_replace=replace,
        ),
    )


def reduce_event(snap: Snapshot, event: EventEnvelope) -> Snapshot:
    """归约单个事件；未知类型只记录到 ``unrecognized_event_kinds``。"""

    if event.kind == EventKind.WORK_ITEM_CREATED:
        work_item_id = event.payload.get("work_item_id")
        if any(w.work_item_id == work_item_id for w in snap.work_items):
            return snap
        state = _work_item_from_created(event)
        return replace(snap, work_items=snap.work_items + (state,))
    if event.kind == EventKind.WORK_ITEM_STATUS_CHANGED:
        return _apply_status_change(snap, event)
    if event.kind == EventKind.SIDE_EFFECT_UNCONFIRMED:
        return _add_unknown_action(snap, event, "requires_reconcile")
    if event.kind == EventKind.PENDING_CHANNEL_LOST:
        return _add_unknown_action(snap, event, "requires_user_restart")
    if event.kind == EventKind.NOTE:
        return snap
    if event.kind == EventKind.SELF_CHANGE_PROPOSED:
        # Self 只由运行时事实组装；模型提案只审计，不能改变派生状态。
        return snap
    if event.kind == EventKind.TASK_CREATED:
        task_id = event.payload.get("task_id")
        if any(t.task_id == task_id for t in snap.tasks):
            return snap
        return replace(snap, tasks=snap.tasks + (_task_from_created(event),))
    if event.kind == EventKind.TASK_STATUS_CHANGED:
        return _apply_task_status_change(snap, event)
    if event.kind == EventKind.TASK_CONSTRAINT_APPENDED:
        return _apply_task_constraint_appended(snap, event)
    if event.kind == EventKind.TASK_WARM_CHANGED:
        return _apply_task_warm_changed(snap, event)
    if event.kind == EventKind.TASK_WARM_RANK_SET:
        return _apply_task_warm_rank_set(snap, event)
    if event.kind == EventKind.TASK_WAITING_SET:
        return _apply_task_waiting_set(snap, event)
    if event.kind == EventKind.TASK_WAITING_CLEARED:
        return _apply_task_waiting_cleared(snap, event)
    if event.kind == EventKind.TASK_AUTHORIZATION_CHANGED:
        return _apply_task_authorization_changed(snap, event)
    if event.kind == EventKind.TASK_COMPLETED:
        return _apply_task_completed(snap, event)
    if event.kind == EventKind.TASK_CANCELLED:
        return _apply_task_cancelled(snap, event)
    if event.kind == EventKind.TASK_ERROR_RECORDED:
        return _apply_task_error_recorded(snap, event)
    if event.kind in (
        EventKind.TASK_CREATION_DENIED,
        EventKind.FOREGROUND_CLAIMED,
        EventKind.FOREGROUND_RELEASED,
    ):
        # 前台归属来自实时表；拒绝创建与前台变更事件只保留审计轨迹。
        return snap
    if event.kind == EventKind.EPISODE_CREATED:
        episode_id = event.payload.get("episode_id")
        if any(e.episode_id == episode_id for e in snap.episodes):
            return snap
        return replace(snap, episodes=snap.episodes + (_episode_from_created(event),))
    if event.kind in (
        EventKind.EPISODE_STATUS_CHANGED,
        EventKind.EPISODE_YIELD_REQUESTED,
        EventKind.EPISODE_CLOSED,
        EventKind.EPISODE_FAILED,
        EventKind.EPISODE_ACTIVATED,
        EventKind.EPISODE_RECOVERING,
    ):
        return _apply_episode_status_change(snap, event)
    if event.kind == EventKind.EPISODE_CHECKPOINT_COMMITTED:
        return _apply_episode_checkpoint(snap, event)
    if event.kind == EventKind.EPISODE_SUSPENDED:
        return _apply_episode_suspended(snap, event)
    if event.kind == EventKind.EPISODE_WAIT_RESOLVED:
        return _apply_episode_wait_resolved(snap, event)
    if event.kind == EventKind.EPISODE_RECONCILE_REQUIRED:
        return _apply_episode_reconcile_required(snap, event)
    if event.kind == EventKind.EPISODE_RECONCILE_RESOLVED:
        return _apply_episode_reconcile_resolved(snap, event)
    if event.kind in (
        EventKind.EPISODE_OWNERSHIP_ACQUIRED,
        EventKind.EPISODE_OWNERSHIP_RELEASED,
        EventKind.EPISODE_SIDE_EFFECT_RECORDED,
        EventKind.LATE_WRITE_REJECTED,
    ):
        # ownership 来自实时表；副作用事实与 stale-write 拒绝在此只用于审计。
        return snap
    if event.kind == EventKind.CONTEXT_SAMPLE_OBSERVED:
        return _apply_context_sample(snap, event)
    if event.kind == EventKind.CONTEXT_GENERATION_BOUNDARY:
        # generation 已在 ContextSample 上，边界事件只保留审计轨迹。
        return snap
    if event.kind not in snap.unrecognized_event_kinds:
        return replace(
            snap,
            unrecognized_event_kinds=snap.unrecognized_event_kinds + (event.kind,),
        )
    return snap


def reduce_decision(snap: Snapshot, decision: Any) -> Snapshot:
    """决策当前只进入审计日志，不派生 Snapshot 状态。"""

    _ = decision
    return snap
