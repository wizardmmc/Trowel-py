"""人工默认态 pilot 的冻结值对象。"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

POLICY_VERSION = "m8-l11-manual-recent-deep-20260723"
AUTOMATIC_DEFAULT = False


class DefaultWorkError(Exception):
    """携带稳定业务 reason code 的预期拒绝。"""

    def __init__(self, code: str, detail: str | None = None) -> None:
        self.code = code
        self.detail = detail or code
        super().__init__(self.detail)


class CandidateStatus(str, Enum):
    NEW = "new"
    SHOWN = "shown"
    ADOPTED = "adopted"
    DISMISSED = "dismissed"
    INVALID = "invalid"
    EXPIRED = "expired"

    @property
    def is_terminal(self) -> bool:
        return self in {
            CandidateStatus.ADOPTED,
            CandidateStatus.DISMISSED,
            CandidateStatus.INVALID,
            CandidateStatus.EXPIRED,
        }


@dataclass(frozen=True)
class RunDefaultPilotCommand:
    command_id: str
    runtime: str
    source_refs: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.command_id.strip():
            raise ValueError("command_id must be non-empty")
        if self.runtime not in {"claude_code", "codex"}:
            raise ValueError("runtime must be claude_code or codex")


@dataclass(frozen=True)
class SampledSource:
    uri_at_generation: str
    memory_id: str
    sampled_content_hash: str
    updated: str
    chars: int
    # 只在当前进程内送入模型；持久化编码器必须丢弃该字段。
    text: str


@dataclass(frozen=True)
class CandidateDraft:
    content: str
    source_refs: tuple[str, ...]
    related_question: str
    why_useful: str
    verification: str
    uncertainty: str


@dataclass(frozen=True)
class Candidate:
    candidate_id: str
    generation_id: str
    content: str
    source_refs: tuple[str, ...]
    related_question: str
    why_useful: str
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


@dataclass(frozen=True)
class GenerationUsage:
    input_tokens: int
    output_tokens: int
    wall_seconds: float
    cost: float | None


@dataclass(frozen=True)
class PilotResult:
    work_item_id: str
    episode_id: str
    generation_id: str
    candidates: tuple[Candidate, ...]

    @property
    def candidate_ids(self) -> tuple[str, ...]:
        return tuple(item.candidate_id for item in self.candidates)


@dataclass(frozen=True)
class BeginGeneration:
    work_item_id: str
    generation_id: str
    result: PilotResult | None = None


@dataclass(frozen=True)
class RecoverableGeneration:
    generation_id: str
    work_item_id: str
    episode_id: str
    runtime: str
    effective_model: str
    validated_output_json: str
    usage: GenerationUsage


@dataclass(frozen=True)
class GateReport:
    status: str
    outcome_count: int
    adopted: int
    invalid: int
    dismissed: int
    adoption_rate: float | None
    invalid_rate: float | None
    total_input_tokens: int
    total_output_tokens: int
    known_cost: float | None
    unknown_cost_generations: int
    automatic_default: bool = AUTOMATIC_DEFAULT
