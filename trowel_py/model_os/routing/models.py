"""快慢模型路由的稳定值对象。"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from trowel_py.model_os.journal import JournalBoundary
from trowel_py.model_os.types import DecisionDisposition
from trowel_py.model_os.work_broker import ModelTier

ROUTE_POLICY_VERSION = "m8-l10-paired-20260723"


class RouteMode(str, Enum):
    OFF = "off"
    SHADOW = "shadow"
    CANARY = "canary"


class UserRoutePreference(str, Enum):
    AUTO = "auto"
    FAST = "fast"
    DEEP = "deep"


class RouteMarker(str, Enum):
    HIGH_IMPACT_IRREVERSIBLE = "high_impact_irreversible"
    EXACT_CONSTRAINT_SEARCH = "exact_constraint_search"
    MULTI_SCENARIO_CONTINGENCY = "multi_scenario_contingency"


class RouteAction(str, Enum):
    USE = "use"
    DENY = "deny"


class RouteReason(str, Enum):
    USER_FAST = "user_fast"
    USER_DEEP = "user_deep"
    MANDATORY_DEEP = "mandatory_deep"
    TRUSTED_PRE_ROUTE = "trusted_pre_route"
    DEFAULT_FAST = "default_fast"
    VALIDATOR_FAILURE = "validator_failure"
    MATERIAL_USER_CORRECTION = "material_user_correction"
    REPEATED_OBJECTIVE_FAILURE = "repeated_objective_failure"
    TOOL_REALITY_CONFLICT = "tool_reality_conflict"
    DEEP_FAILURE_STOP = "deep_failure_stop"
    NO_DEEP_CANDIDATE = "no_deep_candidate"
    ROUTER_OFF = "router_off"


class RouteConfidenceSource(str, Enum):
    USER = "user"
    MANDATORY_POLICY = "mandatory_policy"
    PAIRED_POLICY = "paired_policy"
    TRUSTED_OUTCOME = "trusted_outcome"
    FIXED_BASELINE = "fixed_baseline"


class RouteReviewClass(str, Enum):
    CORRECT = "correct"
    MISSED_DEEP_NEED = "missed_deep_need"
    UNJUSTIFIED_DEEP = "unjustified_deep"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class RouteCandidate:
    tier: ModelTier | None
    model: str | None
    effort: str | None
    budget_cap: dict[str, object] | None = None
    request_model: str | None = None

    def __post_init__(self) -> None:
        if self.model is not None and not self.model.strip():
            raise ValueError("route model must be non-empty when provided")
        if self.effort is not None and not self.effort.strip():
            raise ValueError("route effort must be non-empty when provided")
        if self.request_model is not None and not self.request_model.strip():
            raise ValueError("route request_model must be non-empty when provided")


@dataclass(frozen=True)
class RouteInput:
    work_item_id: str
    task_id: str | None
    runtime: str
    mode: RouteMode
    user_preference: UserRoutePreference
    mandatory_markers: tuple[RouteMarker, ...]
    trusted_pre_route_markers: tuple[RouteMarker, ...]
    trusted_outcomes: tuple[RouteReason, ...]
    previous_tier: ModelTier | None
    fixed_model: str | None
    fixed_effort: str | None
    candidates: tuple[RouteCandidate, ...]
    input_fact_refs: tuple[str, ...]
    evaluation_domain: str
    canary_approved: bool

    def __post_init__(self) -> None:
        if not self.work_item_id.strip() or not self.runtime.strip():
            raise ValueError("route input requires work item and runtime")
        tiers = [item.tier for item in self.candidates]
        if len(tiers) != len(set(tiers)):
            raise ValueError("route candidates must have unique tiers")
        if self.evaluation_domain not in {
            "coding",
            "research",
            "life",
            "other",
            "unknown",
        }:
            raise ValueError("unsupported route evaluation domain")


@dataclass(frozen=True)
class RouteDecision:
    action: RouteAction
    reason: RouteReason
    confidence_source: RouteConfidenceSource
    proposed: RouteCandidate
    actual: RouteCandidate | None
    disposition: DecisionDisposition
    supporting_signal_refs: tuple[str, ...]
    excluded_signal_refs: tuple[str, ...] = ()
    policy_version: str = ROUTE_POLICY_VERSION


@dataclass(frozen=True)
class RecordedRouteDecision:
    decision_id: str
    correlation_id: str | None
    route_input: RouteInput
    decision: RouteDecision


@dataclass(frozen=True)
class RouteGateSnapshot:
    live_episodes: int
    reviewed_episodes: int
    domains: tuple[str, ...]
    trusted_verifier_episodes: int
    trusted_verifier_versions: tuple[str, ...]
    missed_deep_need: int
    unjustified_deep: int
    review_unknown: int
    user_override_total: int
    user_override_executed: int
    actual_match_total: int
    actual_match_executed: int
    ready_for_human_review: bool
    canary_approved: bool
    as_of: JournalBoundary
