"""Model OS journal 的值对象与事件类型常量。"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any


class Provenance(str, Enum):
    """WorkItem 状态归约使用固定来源强度，较弱来源不能覆盖较强来源。"""

    USER_DECISION = "user_decision"
    MACHINE_OBSERVATION = "machine_observation"
    MODEL_HYPOTHESIS = "model_hypothesis"
    UNKNOWN = "unknown"
    STALE = "stale"

    @property
    def strength(self) -> int:
        order = {
            Provenance.USER_DECISION: 4,
            Provenance.MACHINE_OBSERVATION: 3,
            Provenance.MODEL_HYPOTHESIS: 2,
            Provenance.UNKNOWN: 1,
            Provenance.STALE: 0,
        }
        return order[self]


class WorkItemKind(str, Enum):
    TASK = "task"
    DEFAULT = "default"
    MAINTENANCE = "maintenance"
    EXPERIMENT = "experiment"
    INCUBATION = "incubation"


class WorkItemStatus(str, Enum):
    PENDING = "pending"
    READY = "ready"
    RUNNING = "running"
    SUSPENDED = "suspended"
    DONE = "done"
    CANCELLED = "cancelled"
    # FAILED 表示任务级不可重试故障；临时 Episode/tool 故障会回到 READY。
    FAILED = "failed"

    @property
    def is_terminal(self) -> bool:
        return self in (
            WorkItemStatus.DONE,
            WorkItemStatus.CANCELLED,
            WorkItemStatus.FAILED,
        )


class TaskOrigin(str, Enum):
    USER_REQUEST = "user_request"
    SELF_INITIATED = "self_initiated"
    ADOPTED_CANDIDATE = "adopted_candidate"


class TaskStatus(str, Enum):
    BACKLOG = "backlog"
    READY = "ready"
    RUNNING = "running"
    WAITING_USER = "waiting_user"
    WAITING_EVENT = "waiting_event"
    INCUBATING = "incubating"
    DONE = "done"
    CANCELLED = "cancelled"
    ERROR = "error"

    @property
    def is_terminal(self) -> bool:
        return self in (TaskStatus.DONE, TaskStatus.CANCELLED, TaskStatus.ERROR)


class SessionPurpose(str, Enum):
    FOREGROUND = "foreground"
    DEFAULT = "default"
    INCUBATION = "incubation"
    MAINTENANCE = "maintenance"
    EXPERIMENT = "experiment"


class MemoryEligibility(str, Enum):
    ELIGIBLE = "eligible"
    INELIGIBLE = "ineligible"
    ADOPTED = "adopted"


@dataclass(frozen=True)
class WorkItem:
    work_item_id: str
    kind: WorkItemKind
    owner_ref: str
    task_id: str | None
    status: WorkItemStatus
    session_purpose: SessionPurpose
    memory_eligibility: MemoryEligibility
    created_at: str


class EventKind:
    """这里只收纳已知常量；EventEnvelope.kind 保持开放，未知值由 reducer 记录。"""

    WORK_ITEM_CREATED = "work_item.created"
    WORK_ITEM_STATUS_CHANGED = "work_item.status_changed"
    SIDE_EFFECT_UNCONFIRMED = "side_effect.unconfirmed"
    PENDING_CHANNEL_LOST = "pending_channel.lost"
    NOTE = "note"
    # 仅供审计；Self 由运行时事实组装，reducer 不应用该事件。
    SELF_CHANGE_PROPOSED = "self.change_proposed"
    TASK_CREATED = "task.created"
    TASK_STATUS_CHANGED = "task.status_changed"
    TASK_CONSTRAINT_APPENDED = "task.constraint_appended"
    TASK_WARM_CHANGED = "task.warm_changed"
    TASK_WARM_RANK_SET = "task.warm_rank_set"
    TASK_WAITING_SET = "task.waiting_set"
    TASK_WAITING_CLEARED = "task.waiting_cleared"
    TASK_AUTHORIZATION_CHANGED = "task.authorization_changed"
    TASK_COMPLETED = "task.completed"
    TASK_CANCELLED = "task.cancelled"
    TASK_ERROR_RECORDED = "task.error_recorded"
    TASK_CREATION_DENIED = "task.creation_denied"
    # 前台归属以 foreground_claim 表为准，这两个事件只保留审计轨迹。
    FOREGROUND_CLAIMED = "foreground.claimed"
    FOREGROUND_RELEASED = "foreground.released"
    EPISODE_CREATED = "episode.created"
    EPISODE_STATUS_CHANGED = "episode.status_changed"
    # 实时所有权以 leases 表为准，这两个事件只用于审计与 fencing 溯源。
    EPISODE_OWNERSHIP_ACQUIRED = "episode.ownership_acquired"
    EPISODE_OWNERSHIP_RELEASED = "episode.ownership_released"
    EPISODE_YIELD_REQUESTED = "episode.yield_requested"
    EPISODE_CHECKPOINT_COMMITTED = "episode.checkpoint_committed"
    EPISODE_CLOSED = "episode.closed"
    EPISODE_FAILED = "episode.failed"
    EPISODE_SUSPENDED = "episode.suspended"
    EPISODE_WAIT_RESOLVED = "episode.wait_resolved"
    EPISODE_ACTIVATED = "episode.activated"
    EPISODE_RECONCILE_REQUIRED = "episode.reconcile_required"
    EPISODE_RECONCILE_RESOLVED = "episode.reconcile_resolved"
    EPISODE_RECOVERING = "episode.recovering"
    EPISODE_SIDE_EFFECT_RECORDED = "episode.side_effect_recorded"
    # 记录被拒绝的过期 token 写入，不影响派生状态。
    LATE_WRITE_REJECTED = "episode.late_write_rejected"
    # 最新 unavailable 样本也可覆盖旧观测且不触发 yield；归属字段由 envelope 注入。
    CONTEXT_SAMPLE_OBSERVED = "context.sample_observed"
    # 必须先于新一代样本入 journal；reducer 不从该事件单独派生状态。
    CONTEXT_GENERATION_BOUNDARY = "context.generation_boundary"


@dataclass(frozen=True)
class EventEnvelope:
    """字段不可重绑定，但 payload 仍是可变 dict，调用方与 reducer 必须只读。

    Store 持久化脱敏副本；受 fencing 保护的事件必须携带当前租约三元组。
    """

    event_id: str
    kind: str
    occurred_at: str
    source: str
    provenance: Provenance
    policy_version: str
    payload: dict[str, Any]
    work_item_id: str | None = None
    task_id: str | None = None
    episode_id: str | None = None
    native_session_id: str | None = None
    cause_id: str | None = None
    correlation_id: str | None = None
    outcome: str | None = None
    lease_id: str | None = None
    owner: str | None = None
    fencing_token: int | None = None


@dataclass(frozen=True)
class DecisionRecord:
    """自动决策必须先于对应命令持久化，policy_version 用于解释策略差异。"""

    decision_id: str
    kind: str
    decided_at: str
    signals: dict[str, Any]
    candidates: list[Any]
    choice: str
    reason: str
    policy_version: str
    budget_before: dict[str, Any] | None = None
    budget_after: dict[str, Any] | None = None
    work_item_id: str | None = None
    task_id: str | None = None
    episode_id: str | None = None
    cause_id: str | None = None
    correlation_id: str | None = None


@dataclass(frozen=True)
class Lease:
    """同一资源通过 CAS 保证唯一持有者，fencing_token 在重新授予时严格递增。"""

    lease_id: str
    resource_type: str
    resource_id: str
    owner: str
    acquired_at: str
    expires_at: str
    idempotency_key: str | None = None
    fencing_token: int = 0


class SubsystemState(str, Enum):
    """OFF 只表示本次未注入内容，不表示子系统不存在。"""

    INJECTED = "injected"
    OFF = "off"


@dataclass(frozen=True)
class SelfManifest:
    """未知 model/effort 必须保持 None，不能回填旧值；三个 ID 只作位置指针。"""

    identity: str
    version: str
    continuity_note: str
    runtime: str
    model: str | None
    effort: str | None
    subsystems: tuple[str, ...]
    memory_state: SubsystemState
    profile_state: SubsystemState
    native_tools_note: str
    authorization_scope: str
    task_id: str | None = None
    episode_id: str | None = None
    native_session_id: str | None = None


class WaitingSubtype(str, Enum):
    """PendingDescriptor.kind 是权威值，Task subtype 只作镜像。

    APPROVAL 与 INPUT 不可混用；重启或副作用不明时禁止自动恢复。
    """

    INPUT = "input"
    APPROVAL = "approval"
    REQUIRES_USER_RESTART = "requires_user_restart"
    RECONCILE = "reconcile"


@dataclass(frozen=True)
class WaitingCondition:
    """所有等待都要有原因；waiting_user 还需 correlation_id。

    事件等待需要匹配条件，孵化需要问题和准备快照；Episode 驱动的等待必须同时
    携带 subtype 和 episode_id。
    """

    kind: str
    cause: str
    subtype: WaitingSubtype | None = None
    episode_id: str | None = None
    correlation_id: str | None = None
    deadline: str | None = None
    # waiting_event 专用
    condition_kind: str | None = None
    target_ref: str | None = None
    match_params: dict[str, Any] | None = None
    # incubating 专用
    open_question: str | None = None
    preparation_snapshot_ref: str | None = None
    earliest_review_at: str | None = None


@dataclass(frozen=True)
class CompletionEvidence:
    """确认者和证据列表不能为空；USER_REQUEST 还必须由 USER_DECISION 确认。"""

    confirmed_by: str
    confirmation_provenance: Provenance
    evidence_refs: tuple[str, ...] = ()


@dataclass(frozen=True)
class ErrorRecord:
    """error 不会自动回到 ready；last_snapshot_ref 只记录可选的恢复位置。"""

    origin: TaskOrigin
    failure_reason: str
    last_episode_ref: str | None = None
    last_snapshot_ref: str | None = None
    recovery_hint: str | None = None


@dataclass(frozen=True)
class Task:
    """original_goal 不可覆盖，后续修正只能追加约束。"""

    task_id: str
    origin: TaskOrigin
    original_goal: str
    appended_constraints: tuple[str, ...]
    status: TaskStatus
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


class EpisodeStatus(str, Enum):
    STARTING = "starting"
    ACTIVE = "active"
    YIELD_REQUESTED = "yield_requested"
    CHECKPOINTING = "checkpointing"
    SUSPENDED_WAITING_INPUT = "suspended_waiting_input"
    SUSPENDED_WAITING_APPROVAL = "suspended_waiting_approval"
    SUSPENDED_READY = "suspended_ready"
    RECONCILE_REQUIRED = "reconcile_required"
    RECOVERING = "recovering"
    CLOSED = "closed"
    FAILED = "failed"

    @property
    def is_terminal(self) -> bool:
        return self in (EpisodeStatus.CLOSED, EpisodeStatus.FAILED)


class SnapshotSource(str, Enum):
    """RECOVERY_PARTIAL 不调用模型，缺失槽位保持 unknown，只认可有证据的完成项。"""

    COOPERATIVE = "cooperative"
    RECOVERY_PARTIAL = "recovery_partial"


class ReconcileReason(str, Enum):
    """控制通道丢失或副作用结果不明时，必须人工重启或核对现实后再继续。"""

    REQUIRES_USER_RESTART = "requires_user_restart"
    UNKNOWN_SIDE_EFFECT = "unknown_side_effect"


@dataclass(frozen=True)
class PendingDescriptor:
    """kind 是等待类型的事实源；native_generation 关联提出请求时的原生代次。"""

    kind: WaitingSubtype
    native_generation: str | None
    correlation_id: str
    cause: str
    posed_at: str


@dataclass(frozen=True)
class SideEffectRecord:
    """DONE 必须带 evidence_ref；结果不明的副作用在核对现实前禁止重放。"""

    action_ref: str
    idempotency_key: str
    outcome: str
    evidence_ref: str | None = None


@dataclass(frozen=True)
class ArtifactRef:
    kind: str
    ref: str


@dataclass(frozen=True)
class SnapshotRef:
    """reducer 只归约引用；事件 ID 与 hash 用于校验独立表中的已提交 payload。"""

    episode_id: str
    version: int
    committed_event_id: str
    payload_hash: str


@dataclass(frozen=True)
class EpisodeSnapshot:
    """恢复只读到 journal_through_seq；完成项须有证据，transcript 只存引用。

    无法恢复的槽位使用字面值 unknown，不臆造状态。
    """

    work_item_goal: str
    task_constraints_ref: str | None
    current_judgment: str
    completed_with_evidence: tuple[tuple[str, str], ...]
    side_effects: tuple[SideEffectRecord, ...]
    unknowns: tuple[str, ...]
    waiting_condition: PendingDescriptor | None
    next_steps: tuple[str, ...]
    artifacts: tuple[ArtifactRef, ...]
    native_transcript_ref: str | None
    source: SnapshotSource
    journal_through_seq: int
    base_snapshot_ref: SnapshotRef | None = None


@dataclass(frozen=True)
class Episode:
    """Episode 绑定一个 WorkItem；所有权读实时表，last_snapshot_ref 是进度指针。"""

    episode_id: str
    work_item_id: str
    task_id: str | None
    status: EpisodeStatus
    native_session_id: str | None
    ownership_lease_id: str | None
    last_snapshot_ref: SnapshotRef | None
    pending_descriptor: PendingDescriptor | None
    reconcile_reason: ReconcileReason | None
    created_at: str
    updated_at: str
