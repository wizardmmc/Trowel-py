"""只读策略回放的稳定值对象。"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from trowel_py.model_os.explain import DecisionExplanation
from trowel_py.model_os.journal import JournalBoundary


class ReplayStatus(str, Enum):
    MATCHED = "matched"
    DIFFERENT = "different"
    UNAVAILABLE = "replay_unavailable"
    UNSUPPORTED = "policy_unsupported"


@dataclass(frozen=True)
class ReplayedDecision:
    choice: str
    reason_code: str
    target_work_item_id: str | None
    target_task_id: str | None
    target_episode_id: str | None


@dataclass(frozen=True)
class PolicyReplayReport:
    decision_id: str
    decision_kind: str
    recorded_policy_version: str
    replay_policy_version: str
    status: ReplayStatus
    recorded: ReplayedDecision
    replayed: ReplayedDecision | None
    input_refs: tuple[str, ...]
    input_boundary: JournalBoundary | None
    missing_fields: tuple[str, ...] = ()


@dataclass(frozen=True)
class EvidenceFact:
    event_id: str
    domain: str
    kind: str
    source_class: str
    provenance: str
    policy_version: str
    model_version: str | None
    work_item_id: str | None
    task_id: str | None
    episode_id: str | None
    cause_id: str | None
    correlation_id: str | None
    outcome: str | None
    evidence_refs: tuple[str, ...]


@dataclass(frozen=True)
class ScopeExplanation:
    subject_kind: str
    subject_id: str
    as_of: JournalBoundary
    decisions: tuple[DecisionExplanation, ...]
    facts: tuple[EvidenceFact, ...]
    source_counts: dict[str, int]
    truncated: bool


@dataclass(frozen=True)
class MetricRatio:
    name: str
    numerator: int
    denominator: int
    unknown: int


@dataclass(frozen=True)
class CostSummary:
    known_total: float | None
    known_count: int
    unknown_count: int
    sources: tuple[str, ...]


@dataclass(frozen=True)
class MetricDimension:
    name: str
    ratios: tuple[MetricRatio, ...]
    policy_versions: tuple[str, ...]
    model_versions: tuple[str, ...]
    cost: CostSummary


@dataclass(frozen=True)
class MetricsReport:
    window_start: str
    window_end: str
    as_of: JournalBoundary
    dimensions: tuple[MetricDimension, ...]
