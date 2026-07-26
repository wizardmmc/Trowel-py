"""Yield 控制层的冻结输入、输出与单 turn 内存态。"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from trowel_py.model_os.types import PendingDescriptor


class YieldControlError(RuntimeError):
    pass


class ForceYieldReason(str, Enum):
    CONTEXT_LIMIT = "context_limit"
    HARD_BUDGET = "hard_budget"
    USER_PREEMPT = "user_preempt"
    RUNTIME_TIMEOUT = "runtime_timeout"
    SHUTDOWN = "shutdown"


class YieldSuggestedState(str, Enum):
    READY = "ready"
    WAITING_EVENT = "waiting_event"
    WAITING_USER = "waiting_user"
    DONE = "done"


@dataclass(frozen=True)
class SoftYieldPolicy:
    threshold_ratio: float = 0.8
    deadline_seconds: float = 120.0
    policy_version: str = "yield-soft-v1"

    def __post_init__(self) -> None:
        if not 0 < self.threshold_ratio <= 1:
            raise ValueError("soft yield threshold must be in (0, 1]")
        if self.deadline_seconds <= 0:
            raise ValueError("soft yield deadline must be positive")
        if not self.policy_version.strip():
            raise ValueError("soft yield policy_version must be non-empty")


@dataclass(frozen=True)
class YieldWaitingCondition:
    cause: str
    condition_kind: str
    target_ref: str
    match_params: dict[str, Any] | None = None
    deadline: str | None = None

    def __post_init__(self) -> None:
        if not self.cause.strip() or not self.condition_kind.strip() or not self.target_ref.strip():
            raise ValueError("waiting condition requires cause, kind and target")


@dataclass(frozen=True)
class YieldProposal:
    reason: str
    suggested_task_state: YieldSuggestedState
    waiting_condition: YieldWaitingCondition | None
    current_judgment: str
    next_steps: tuple[str, ...]
    continue_same_task: bool

    def __post_init__(self) -> None:
        if not self.reason.strip() or not self.current_judgment.strip():
            raise ValueError("reason and current_judgment must be non-empty")
        if len(self.next_steps) > 3 or any(not step.strip() for step in self.next_steps):
            raise ValueError("next_steps must contain at most three non-empty items")
        if (
            self.suggested_task_state == YieldSuggestedState.WAITING_EVENT
            and self.waiting_condition is None
        ):
            raise ValueError("waiting_event requires a verifiable waiting_condition")


@dataclass(frozen=True)
class TurnRegistration:
    session_id: str
    episode_id: str
    runtime: str
    turn_id: str
    generation: str
    native_session_id: str
    ownership_lease_id: str
    ownership_owner: str
    ownership_token: int
    work_lease_id: str | None = None
    context_generation: int = 0

    def __post_init__(self) -> None:
        if self.runtime not in {"claude_code", "codex"}:
            raise ValueError(f"unsupported runtime {self.runtime!r}")
        required = (
            self.session_id,
            self.episode_id,
            self.turn_id,
            self.generation,
            self.native_session_id,
            self.ownership_lease_id,
            self.ownership_owner,
        )
        if any(not value for value in required) or self.ownership_token <= 0:
            raise ValueError("turn registration requires complete durable identity")
        if self.context_generation < 0:
            raise ValueError("context generation cannot be negative")


@dataclass(frozen=True)
class YieldReceipt:
    status: str
    episode_id: str | None
    checkpoint_ref: str | None = None


@dataclass
class TurnState:
    registration: TurnRegistration
    started_monotonic: float = field(default_factory=time.monotonic)
    proposal: YieldProposal | None = None
    force_reason: ForceYieldReason | None = None
    interrupt_correlation_id: str | None = None
    interrupt_decision_id: str | None = None
    interrupt_attempted: bool = False
    interrupt_sent: bool = False
    terminal_type: str | None = None
    unresolved_tools: dict[str, str] = field(default_factory=dict)
    unresolved_subagents: set[str] = field(default_factory=set)
    final_receipt: YieldReceipt | None = None
    work_lease_released: bool = False
    safe_interrupt_window: bool = True
    requested_monotonic: float | None = None
    boundary_elapsed_ms: int | None = None
    pending_descriptor: PendingDescriptor | None = None
    context_generation: int = 0
    soft_request_generation: int | None = None
    soft_request_decision_id: str | None = None
    soft_request_correlation_id: str | None = None
    soft_request_attempted: bool = False
    soft_request_sent: bool = False
    soft_request_unknown: bool = False
    proposal_context_generation: int | None = None
    context_boundary_ids: set[str] = field(default_factory=set)


InterruptRuntime = Callable[[str], Awaitable[None]]
SteerRuntime = Callable[..., Awaitable[None]]
ReleaseWorkLease = Callable[[str], Awaitable[None] | None]
