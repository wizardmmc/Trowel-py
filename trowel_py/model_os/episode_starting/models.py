"""StartEpisode 的冻结输入、runtime 身份与恢复进度。"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from trowel_py.model_os.types import (
    EpisodeSnapshot,
    MemoryEligibility,
    SessionPurpose,
    SnapshotRef,
)


class StartStage(str, Enum):
    INTENT = "intent"
    NATIVE_REQUESTED = "native_requested"
    NATIVE_RESPONDED = "native_responded"
    BINDING_PERSISTED = "binding_persisted"
    FIRST_TURN_REQUESTED = "first_turn_requested"
    FIRST_TURN_ACCEPTED = "first_turn_accepted"
    TERMINAL = "terminal"
    UNKNOWN = "unknown_requires_reconcile"


@dataclass(frozen=True)
class StartEpisodeCommand:
    work_item_id: str
    task_id: str | None
    previous_episode_id: str | None
    previous_snapshot_ref: SnapshotRef | None
    runtime: str
    model: str | None
    effort: str | None
    memory_enabled: bool
    profile_enabled: bool
    workdir: str
    session_purpose: SessionPurpose
    memory_eligibility: MemoryEligibility
    permission: str
    idempotency_key: str
    resume_from: str | None = None
    owner: str = "episode-runner"
    ownership_ttl_seconds: int = 600

    def __post_init__(self) -> None:
        if self.runtime not in {"claude_code", "codex"}:
            raise ValueError(f"unsupported runtime {self.runtime!r}")
        required = (
            self.work_item_id,
            self.workdir,
            self.permission,
            self.idempotency_key,
            self.owner,
        )
        if any(not value.strip() for value in required):
            raise ValueError("StartEpisodeCommand requires non-empty identity fields")
        if not isinstance(self.memory_enabled, bool) or not isinstance(
            self.profile_enabled, bool
        ):
            raise ValueError("memory/profile switches must be boolean")
        if not isinstance(self.session_purpose, SessionPurpose):
            raise ValueError("session_purpose must be a SessionPurpose")
        if not isinstance(self.memory_eligibility, MemoryEligibility):
            raise ValueError("memory_eligibility must be a MemoryEligibility")
        if self.ownership_ttl_seconds <= 0:
            raise ValueError("ownership_ttl_seconds must be positive")
        if self.resume_from is not None:
            raise ValueError("StartEpisodeCommand always starts fresh; resume is forbidden")
        paired = self.previous_episode_id is not None
        if paired != (self.previous_snapshot_ref is not None):
            raise ValueError(
                "previous_episode_id and previous_snapshot_ref must be provided together"
            )
        if self.previous_snapshot_ref is not None and not isinstance(
            self.previous_snapshot_ref, SnapshotRef
        ):
            raise ValueError("previous_snapshot_ref must reference a committed snapshot")
        if (
            self.previous_snapshot_ref is not None
            and self.previous_snapshot_ref.episode_id != self.previous_episode_id
        ):
            raise ValueError("previous_snapshot_ref does not belong to previous_episode_id")


@dataclass(frozen=True)
class NativeSessionIdentity:
    agent_session_id: str
    runtime: str
    native_session_id: str | None
    runtime_generation: str
    runtime_pid: int | None = None
    runtime_pgid: int | None = None

    def __post_init__(self) -> None:
        if self.runtime not in {"claude_code", "codex"}:
            raise ValueError(f"unsupported runtime {self.runtime!r}")
        if not self.agent_session_id or not self.runtime_generation:
            raise ValueError("native identity requires session and generation")


@dataclass(frozen=True)
class StartProgress:
    idempotency_key: str
    correlation_id: str
    stage: StartStage
    episode_id: str | None = None
    identity: NativeSessionIdentity | None = None
    turn_id: str | None = None
    degraded_native_compact: bool = False


@dataclass(frozen=True)
class EpisodeContext:
    self_text: str
    work_item_id: str
    task_id: str | None
    episode_id: str
    original_goal: str
    user_decisions: tuple[str, ...]
    previous_snapshot: EpisodeSnapshot | None
    memory_enabled: bool
    profile_enabled: bool

    def render(self) -> str:
        decisions = "\n".join(f"- {item}" for item in self.user_decisions) or "- 无"
        if self.previous_snapshot is None:
            snapshot = "无；这是该 WorkItem 的首段。"
        else:
            completed = "\n".join(
                f"- {action}（证据：{evidence}）"
                for action, evidence in self.previous_snapshot.completed_with_evidence
            ) or "- 无已确认完成项"
            next_steps = "\n".join(
                f"- {step}" for step in self.previous_snapshot.next_steps
            ) or "- 无"
            snapshot = (
                f"当前判断：{self.previous_snapshot.current_judgment}\n"
                f"已确认完成：\n{completed}\n"
                f"下一步：\n{next_steps}"
            )
        return (
            f"{self.self_text}\n\n"
            "# Episode Context\n\n"
            f"- WorkItem: {self.work_item_id}\n"
            f"- Task: {self.task_id or '无（system WorkItem）'}\n"
            f"- Episode: {self.episode_id}\n"
            f"- Memory: {'on' if self.memory_enabled else 'off'}\n"
            f"- Profile: {'on' if self.profile_enabled else 'off'}\n\n"
            "## 不可变原始目标\n"
            f"{self.original_goal}\n\n"
            "## 用户后补决定\n"
            f"{decisions}\n\n"
            "## 最新已提交 snapshot\n"
            f"{snapshot}\n\n"
            "## 核查边界\n"
            "snapshot 可能有错；当前事实和工具结果优先。先核查当前事实和工具结果，"
            "再继续工作，不自动重放上一段未确认的写动作。"
        )
