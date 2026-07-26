"""确定性注意力调度使用的值对象。"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from trowel_py.model_os.journal import JournalBoundary

ATTENTION_POLICY_VERSION = "attention-v0"


class ScheduleAction(str, Enum):
    CONTINUE = "continue"
    REQUEST_YIELD = "request_yield"
    DISPATCH = "dispatch"
    IDLE = "idle"


class ScheduleReason(str, Enum):
    ACTIVE_FOCUS = "active_focus"
    USER_OVERRIDE_CURRENT = "user_override_current"
    USER_OVERRIDE_SWITCH = "user_override_switch"
    FAIR_SERVICE = "fair_service"
    RESOURCE_DEFERRED = "resource_deferred"
    NO_READY_WORK = "no_ready_work"
    STALE_TRIGGER = "stale_trigger"
    TARGET_NOT_RUNNABLE = "target_not_runnable"


@dataclass(frozen=True)
class ScheduleCandidate:
    work_item_id: str
    task_id: str
    priority: int
    warm_rank: int | None
    created_at: str
    ready_epoch_ref: str
    virtual_service_segments: int
    suspended_episode_id: str | None = None

    def __post_init__(self) -> None:
        if self.virtual_service_segments < 0:
            raise ValueError("virtual service segments cannot be negative")


@dataclass(frozen=True)
class ScheduleInput:
    trigger_event_ref: str
    journal_boundary: JournalBoundary
    candidates: tuple[ScheduleCandidate, ...]
    current_foreground_task_id: str | None = None
    previous_foreground_task_id: str | None = None
    user_override_task_id: str | None = None


@dataclass(frozen=True)
class ScheduleDecision:
    action: ScheduleAction
    reason: ScheduleReason
    trigger_event_ref: str
    journal_boundary: JournalBoundary
    candidate_summaries: tuple[ScheduleCandidate, ...]
    policy_version: str = ATTENTION_POLICY_VERSION
    target_work_item_id: str | None = None
    target_task_id: str | None = None
    target_episode_id: str | None = None


@dataclass(frozen=True)
class RecordedScheduleDecision:
    decision_id: str
    correlation_id: str | None
    intent_event_id: str | None
    decision: ScheduleDecision


@dataclass(frozen=True)
class ScheduleOutcome:
    recorded: RecordedScheduleDecision
    result_code: str

    @property
    def decision(self) -> ScheduleDecision:
        return self.recorded.decision
