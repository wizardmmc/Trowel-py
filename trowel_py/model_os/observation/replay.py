"""只运行纯 policy 的 Decision 回放；不调用模型、工具或写入 Store。"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from trowel_py.model_os.observation.models import (
    PolicyReplayReport,
    ReplayedDecision,
    ReplayStatus,
)
from trowel_py.model_os.scheduling import (
    ATTENTION_POLICY_VERSION,
    ScheduleInput,
    decide_schedule,
    schedule_decision_from_record,
)
from trowel_py.model_os.journal import (
    JournalBoundary,
    public_journal_label,
    public_journal_ref,
)
from trowel_py.model_os.routing import (
    ROUTE_POLICY_VERSION,
    RouteCandidate,
    RouteInput,
    RouteMarker,
    RouteMode,
    RouteReason,
    UserRoutePreference,
    decide_route,
)
from trowel_py.model_os.work_broker import ModelTier
from trowel_py.model_os.types import DecisionRecord


@dataclass(frozen=True)
class ReplayEvaluation:
    replayed: ReplayedDecision | None
    input_boundary: JournalBoundary | None = None
    missing_fields: tuple[str, ...] = ()


ReplayEvaluator = Callable[[DecisionRecord], ReplayEvaluation]


class PolicyReplayRegistry:
    """显式注册纯 policy 版本；注册表本身不执行 I/O。"""

    def __init__(
        self,
        evaluators: dict[tuple[str, str], ReplayEvaluator] | None = None,
    ) -> None:
        self._evaluators = dict(evaluators or {})

    def with_policy(
        self,
        decision_kind: str,
        policy_version: str,
        evaluator: ReplayEvaluator,
    ) -> "PolicyReplayRegistry":
        updated = dict(self._evaluators)
        updated[(decision_kind, policy_version)] = evaluator
        return PolicyReplayRegistry(updated)

    def evaluator(
        self,
        decision_kind: str,
        policy_version: str,
    ) -> ReplayEvaluator | None:
        return self._evaluators.get((decision_kind, policy_version))


def _public_view(view: ReplayedDecision) -> ReplayedDecision:
    return ReplayedDecision(
        choice=public_journal_label(view.choice),
        reason_code=public_journal_label(view.reason_code),
        target_work_item_id=public_journal_ref(view.target_work_item_id),
        target_task_id=public_journal_ref(view.target_task_id),
        target_episode_id=public_journal_ref(view.target_episode_id),
    )


def _recorded_view(record) -> ReplayedDecision:
    return _public_view(
        ReplayedDecision(
            choice=record.choice,
            reason_code=record.reason,
            target_work_item_id=record.work_item_id,
            target_task_id=record.task_id,
            target_episode_id=record.episode_id,
        )
    )


def _schedule_view(decision) -> ReplayedDecision:
    return ReplayedDecision(
        choice=decision.action.value,
        reason_code=decision.reason.value,
        target_work_item_id=decision.target_work_item_id,
        target_task_id=decision.target_task_id,
        target_episode_id=decision.target_episode_id,
    )


def _route_input(record) -> RouteInput | None:
    raw = next(
        (
            item
            for item in record.candidates
            if isinstance(item, dict) and item.get("role") == "input"
        ),
        None,
    )
    if raw is None:
        return None
    try:
        required_strings = ("work_item_id", "runtime", "mode", "preference")
        if not all(isinstance(raw.get(field), str) for field in required_strings):
            return None
        if raw.get("task_id") is not None and not isinstance(raw["task_id"], str):
            return None
        for field in ("fixed_model", "fixed_effort", "previous_tier"):
            if raw.get(field) is not None and not isinstance(raw[field], str):
                return None
        list_fields = (
            "candidates",
            "mandatory_markers",
            "trusted_pre_route_markers",
            "trusted_outcomes",
            "input_fact_refs",
        )
        if not all(isinstance(raw.get(field), list) for field in list_fields):
            return None
        if not isinstance(raw.get("evaluation_domain"), str) or not isinstance(
            raw.get("canary_approved"), bool
        ):
            return None
        if not all(
            isinstance(value, str)
            for field in list_fields[1:]
            for value in raw[field]
        ):
            return None
        raw_candidates = [
            item
            for item in raw["candidates"]
            if isinstance(item, dict) and item.get("role") == "configured"
        ]
        if len(raw_candidates) != len(raw["candidates"]):
            return None
        for item in raw_candidates:
            for field in ("tier", "model", "effort", "request_model"):
                if item.get(field) is not None and not isinstance(item[field], str):
                    return None
            if item.get("budget_cap") is not None and not isinstance(
                item["budget_cap"], dict
            ):
                return None
        configured = tuple(
            RouteCandidate(
                tier=(
                    ModelTier(item["tier"])
                    if item.get("tier") is not None
                    else None
                ),
                model=item.get("model") if isinstance(item.get("model"), str) else None,
                effort=(
                    item.get("effort")
                    if isinstance(item.get("effort"), str)
                    else None
                ),
                budget_cap=(
                    item.get("budget_cap")
                    if isinstance(item.get("budget_cap"), dict)
                    else None
                ),
                request_model=(
                    item.get("request_model")
                    if isinstance(item.get("request_model"), str)
                    else None
                ),
            )
            for item in raw_candidates
        )
        return RouteInput(
            work_item_id=raw["work_item_id"],
            task_id=raw.get("task_id"),
            runtime=raw["runtime"],
            mode=RouteMode(raw["mode"]),
            user_preference=UserRoutePreference(raw["preference"]),
            mandatory_markers=tuple(
                RouteMarker(value) for value in raw["mandatory_markers"]
            ),
            trusted_pre_route_markers=tuple(
                RouteMarker(value) for value in raw["trusted_pre_route_markers"]
            ),
            trusted_outcomes=tuple(
                RouteReason(value) for value in raw["trusted_outcomes"]
            ),
            previous_tier=(
                ModelTier(raw["previous_tier"])
                if raw.get("previous_tier") is not None
                else None
            ),
            fixed_model=raw.get("fixed_model"),
            fixed_effort=raw.get("fixed_effort"),
            candidates=configured,
            input_fact_refs=tuple(raw["input_fact_refs"]),
            evaluation_domain=raw["evaluation_domain"],
            canary_approved=raw["canary_approved"],
        )
    except (KeyError, TypeError, ValueError):
        return None


def _route_view(route_input: RouteInput, decision) -> ReplayedDecision:
    choice = (
        decision.proposed.tier.value
        if decision.proposed.tier is not None
        else decision.action.value
    )
    return ReplayedDecision(
        choice=choice,
        reason_code=decision.reason.value,
        target_work_item_id=route_input.work_item_id,
        target_task_id=route_input.task_id,
        target_episode_id=None,
    )


def _attention_missing(record) -> tuple[str, ...]:
    input_row = next(
        (
            item
            for item in record.candidates
            if isinstance(item, dict) and item.get("role") == "input"
        ),
        None,
    )
    missing: list[str] = []
    if any(
        isinstance(item, dict)
        and item.get("role", "candidate") == "candidate"
        and "created_at_b64" not in item
        for item in record.candidates
    ):
        missing.append("candidates.created_at")
    if input_row is None or "current_foreground_task_id" not in input_row:
        missing.append("current_foreground_task_id")
    if input_row is None or "journal_decision_seq" not in input_row:
        missing.append("journal_boundary.decision_seq")
    return tuple(missing)


def _replay_route(record: DecisionRecord) -> ReplayEvaluation:
    route_input = _route_input(record)
    if route_input is None:
        return ReplayEvaluation(None, missing_fields=("route_input",))
    return ReplayEvaluation(_route_view(route_input, decide_route(route_input)))


def _replay_attention(record: DecisionRecord) -> ReplayEvaluation:
    missing = _attention_missing(record)
    if missing:
        return ReplayEvaluation(None, missing_fields=missing)
    try:
        frozen = schedule_decision_from_record(record)
    except (KeyError, TypeError, ValueError):
        return ReplayEvaluation(
            None,
            missing_fields=("schedule_input.invalid",),
        )
    replayed_decision = decide_schedule(
        ScheduleInput(
            trigger_event_ref=frozen.trigger_event_ref,
            journal_boundary=frozen.journal_boundary,
            candidates=frozen.candidate_summaries,
            current_foreground_task_id=frozen.current_foreground_task_id,
            previous_foreground_task_id=frozen.previous_foreground_task_id,
            user_override_task_id=frozen.user_override_task_id,
        )
    )
    return ReplayEvaluation(
        _schedule_view(replayed_decision),
        input_boundary=frozen.journal_boundary,
    )


DEFAULT_REPLAY_REGISTRY = (
    PolicyReplayRegistry()
    .with_policy("attention.schedule", ATTENTION_POLICY_VERSION, _replay_attention)
    .with_policy("cognitive.route", ROUTE_POLICY_VERSION, _replay_route)
)


def replay_policy_decision(
    store,
    decision_id: str,
    *,
    policy_version: str | None = None,
    boundary: JournalBoundary | None = None,
    registry: PolicyReplayRegistry | None = None,
) -> PolicyReplayReport:
    fixed = boundary or store.journal_boundary()
    record = store.read_decision_record(decision_id, boundary=fixed)
    if record is None:
        raise LookupError("decision not found")

    target_version = policy_version or record.policy_version
    recorded = _recorded_view(record)
    raw_refs = record.signals.get("refs", ()) if isinstance(record.signals, dict) else ()
    if not isinstance(raw_refs, (list, tuple)):
        raw_refs = ()
    input_refs = tuple(
        public_journal_ref(value) or "unknown"
        for value in raw_refs
    )
    evaluator = (registry or DEFAULT_REPLAY_REGISTRY).evaluator(
        record.kind,
        target_version,
    )
    if evaluator is None:
        return PolicyReplayReport(
            decision_id=public_journal_ref(decision_id) or "unknown",
            decision_kind=public_journal_label(record.kind),
            recorded_policy_version=public_journal_label(record.policy_version),
            replay_policy_version=public_journal_label(target_version),
            status=ReplayStatus.UNSUPPORTED,
            recorded=recorded,
            replayed=None,
            input_refs=input_refs,
            input_boundary=None,
        )
    evaluation = evaluator(record)
    if evaluation.replayed is None:
        return PolicyReplayReport(
            decision_id=public_journal_ref(decision_id) or "unknown",
            decision_kind=public_journal_label(record.kind),
            recorded_policy_version=public_journal_label(record.policy_version),
            replay_policy_version=public_journal_label(target_version),
            status=ReplayStatus.UNAVAILABLE,
            recorded=recorded,
            replayed=None,
            input_refs=input_refs,
            input_boundary=evaluation.input_boundary,
            missing_fields=tuple(
                public_journal_label(value) for value in evaluation.missing_fields
            ),
        )
    replayed = _public_view(evaluation.replayed)
    return PolicyReplayReport(
        decision_id=public_journal_ref(decision_id) or "unknown",
        decision_kind=public_journal_label(record.kind),
        recorded_policy_version=public_journal_label(record.policy_version),
        replay_policy_version=public_journal_label(target_version),
        status=(
            ReplayStatus.MATCHED if replayed == recorded else ReplayStatus.DIFFERENT
        ),
        recorded=recorded,
        replayed=replayed,
        input_refs=input_refs,
        input_boundary=evaluation.input_boundary,
    )
