"""显式单轮孵化的稳定值对象。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from trowel_py.model_os.candidates import CandidateStatus
from trowel_py.model_os.types import SnapshotRef
from trowel_py.model_os.work_broker import BudgetDimensions
from trowel_py.model_os.waking import WakeConditionKind

POLICY_VERSION = "m8-l13-manual-single-deep-20260723"
REFRAME_POLICY = "m8-l13-constraint-failure-falsifiable-v1"
AUTOMATIC_INCUBATION = False
AUTOMATIC_TASK_TYPES: tuple[str, ...] = ()
MAX_SCHEDULED_CYCLES = 1


class IncubationError(Exception):
    def __init__(self, code: str, detail: str | None = None) -> None:
        self.code = code
        self.detail = detail or code
        super().__init__(self.detail)


class IncubationPlanStatus(str, Enum):
    PENDING_WAKE = "pending_wake"
    READY = "ready"
    RUNNING = "running"
    AWAITING_REVIEW = "awaiting_review"
    STOPPED = "stopped"
    FAILED = "failed"
    CANCELLED = "cancelled"
    RESULT_UNKNOWN = "result_unknown"

    @property
    def is_terminal(self) -> bool:
        return self in {
            IncubationPlanStatus.STOPPED,
            IncubationPlanStatus.FAILED,
            IncubationPlanStatus.CANCELLED,
            IncubationPlanStatus.RESULT_UNKNOWN,
        }


@dataclass(frozen=True)
class IncubationWakeCondition:
    kind: WakeConditionKind
    target_ref: str
    match_params: dict[str, Any]
    due_at: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.kind, WakeConditionKind):
            raise IncubationError("wake_condition_missing")
        if not self.target_ref.strip():
            raise IncubationError("wake_condition_missing")
        if self.kind is WakeConditionKind.TIME and self.due_at is None:
            raise IncubationError("wake_condition_missing")
        if self.due_at is not None:
            parse_instant(self.due_at, "wake condition due_at")


@dataclass(frozen=True)
class CreateIncubationPlanCommand:
    command_id: str
    task_id: str
    prepared_snapshot_ref: SnapshotRef
    unresolved_question: str
    wake_condition: IncubationWakeCondition
    deadline: str | None
    budget: BudgetDimensions
    runtime: str
    occurred_at: datetime

    def __post_init__(self) -> None:
        if not self.command_id.strip():
            raise ValueError("command_id must be non-empty")
        if not self.task_id.strip():
            raise IncubationError("task_missing")
        if not isinstance(self.prepared_snapshot_ref, SnapshotRef):
            raise IncubationError("snapshot_missing")
        if not self.unresolved_question.strip():
            raise IncubationError("unresolved_question_missing")
        if self.runtime not in {"claude_code", "codex"}:
            raise ValueError("runtime must be claude_code or codex")
        if self.occurred_at.tzinfo is None:
            raise ValueError("occurred_at must be timezone-aware")
        if self.deadline is not None:
            parse_instant(self.deadline, "deadline")
        if self.budget.calls != 1:
            raise IncubationError("budget_denied")
        if any(
            value is not None
            for value in (
                self.budget.tokens,
                self.budget.cost,
                self.budget.wall_seconds,
            )
        ):
            raise IncubationError("budget_denied")


@dataclass(frozen=True)
class IncubationPlan:
    plan_id: str
    command_id: str
    task_id: str
    work_item_id: str
    prepared_snapshot_ref: SnapshotRef
    unresolved_question: str
    wake_condition: IncubationWakeCondition
    deadline: str | None
    budget: BudgetDimensions
    runtime: str
    cycle: int
    max_scheduled_cycles: int
    status: IncubationPlanStatus
    stop_reason: str | None
    episode_id: str | None
    model_called: bool
    broker_settled: bool
    effective_model: str | None
    usage: IncubationUsage | None
    policy_version: str
    reframe_policy: str
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class IncubationCandidateDraft:
    proposal: str
    source_refs: tuple[str, ...]
    new_points: tuple[str, ...]
    verification: str
    uncertainty: str


@dataclass(frozen=True)
class IncubationCandidate:
    candidate_id: str
    plan_id: str
    cycle: int
    proposal: str
    source_refs: tuple[str, ...]
    new_points: tuple[str, ...]
    verification: str
    uncertainty: str
    runtime: str
    effective_model: str
    tier: str
    policy_version: str
    status: CandidateStatus
    created_at: str
    shown_at: str | None = None
    outcome_at: str | None = None
    outcome_reason: str | None = None
    expires_at: str | None = None
    cleaned_at: str | None = None


@dataclass(frozen=True)
class IncubationUsage:
    input_tokens: int
    output_tokens: int
    wall_seconds: float
    cost: float | None


@dataclass(frozen=True)
class IncubationResult:
    plan: IncubationPlan
    candidate: IncubationCandidate | None


@dataclass(frozen=True)
class IncubationGateReport:
    status: str
    outcome_count: int
    adopted: int
    invalid: int
    dismissed: int
    adoption_rate: float | None
    invalid_rate: float | None
    verification_success: int
    verification_total: int
    automatic_incubation: bool = AUTOMATIC_INCUBATION


def parse_instant(value: str, label: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{label} must be an ISO timestamp") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"{label} must include a timezone")
    return parsed.astimezone(timezone.utc)
