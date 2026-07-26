"""在 fresh Episode 边界组装权威证据并记录路由决定。"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone

from trowel_py.model_os.cognitive_signals import (
    CognitiveSignal,
    ExecutionOutcomePayload,
    FailureMode,
    SignalFamily,
    ToolFailureCategory,
)
from trowel_py.model_os.routing.config import RoutingConfig
from trowel_py.model_os.routing.journal import (
    RecordedRouteDecision,
    record_route_decision,
    route_tier_for_episode,
)
from trowel_py.model_os.routing.models import (
    ROUTE_POLICY_VERSION,
    RouteInput,
    RouteMarker,
    RouteReason,
    UserRoutePreference,
)
from trowel_py.model_os.routing.policy import decide_route
from trowel_py.model_os.routing.read_model import build_route_gate
from trowel_py.model_os.signal_assessment import (
    build_route_evidence_bundle,
    comparable_failure_series,
    derive_outcome_assessment,
)
from trowel_py.model_os.signal_projection import read_signal_from_event_payload


@dataclass(frozen=True)
class RouteRequest:
    preference: UserRoutePreference = UserRoutePreference.AUTO
    mandatory_markers: tuple[RouteMarker, ...] = ()
    trusted_pre_route_markers: tuple[RouteMarker, ...] = ()
    evaluation_domain: str = "unknown"
    input_fact_refs: tuple[str, ...] = ()


class CognitiveRouter:
    def __init__(self, store, config: RoutingConfig) -> None:
        self._store = store
        self._config = config

    def route(
        self,
        *,
        idempotency_key: str,
        work_item_id: str,
        task_id: str | None,
        previous_episode_id: str | None,
        runtime: str,
        fixed_model: str | None,
        fixed_effort: str | None,
        request: RouteRequest,
    ) -> RecordedRouteDecision:
        outcomes, supporting, excluded = self._outcome_evidence(
            task_id, previous_episode_id
        )
        refs = tuple(dict.fromkeys((*request.input_fact_refs, *supporting)))
        gate = build_route_gate(self._store)
        route_input = RouteInput(
            work_item_id=work_item_id,
            task_id=task_id,
            runtime=runtime,
            mode=self._config.mode_for(runtime),
            user_preference=request.preference,
            mandatory_markers=request.mandatory_markers,
            trusted_pre_route_markers=request.trusted_pre_route_markers,
            trusted_outcomes=outcomes,
            previous_tier=(
                route_tier_for_episode(self._store, previous_episode_id)
                if previous_episode_id is not None
                else None
            ),
            fixed_model=fixed_model,
            fixed_effort=fixed_effort,
            candidates=self._config.candidates(runtime),
            input_fact_refs=refs,
            evaluation_domain=request.evaluation_domain,
            canary_approved=gate.canary_approved,
        )
        decision = decide_route(route_input)
        if excluded:
            decision = replace(decision, excluded_signal_refs=excluded)
        return record_route_decision(
            self._store,
            f"episode-route:{idempotency_key}",
            route_input,
            decision,
        )

    def _outcome_evidence(
        self,
        task_id: str | None,
        previous_episode_id: str | None,
    ) -> tuple[tuple[RouteReason, ...], tuple[str, ...], tuple[str, ...]]:
        if task_id is None or previous_episode_id is None:
            return (), (), ()
        episode_signals = self._signals(episode_id=previous_episode_id)
        if not episode_signals:
            return (), (), ()
        terminal = next(
            (
                item
                for item in reversed(episode_signals)
                if item.kind.family is SignalFamily.TURN_OUTCOME
            ),
            None,
        )
        if terminal is None:
            bundle = build_route_evidence_bundle(
                tuple(episode_signals),
                policy_version=ROUTE_POLICY_VERSION,
                actual_outcome=None,
                negative_sample_marker=False,
            )
            return (), bundle.supporting_signal_refs, bundle.excluded_ids
        attempt = tuple(
            item for item in episode_signals if item.attempt_id == terminal.attempt_id
        )
        as_of = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        attribution, _ = derive_outcome_assessment(
            attempt,
            terminal,
            as_of=as_of,
            attribution_policy_version=ROUTE_POLICY_VERSION,
        )
        bundle = build_route_evidence_bundle(
            tuple(episode_signals),
            policy_version=ROUTE_POLICY_VERSION,
            actual_outcome=attribution,
            negative_sample_marker=False,
        )
        outcomes: list[RouteReason] = []
        if any(
            item.kind.family is SignalFamily.VALIDATOR_OUTCOME
            and item.kind.subtype == "fail"
            and item.signal_id in bundle.eligible_trigger_ids
            for item in attempt
        ):
            outcomes.append(RouteReason.VALIDATOR_FAILURE)
        if any(
            item.kind.family is SignalFamily.USER_FEEDBACK
            and item.kind.subtype == "correction"
            for item in attempt
        ):
            outcomes.append(RouteReason.MATERIAL_USER_CORRECTION)
        all_task_signals = self._signals(task_id=task_id)
        if any(
            len(comparable_failure_series(item, tuple(all_task_signals))) >= 2
            for item in attempt
        ):
            outcomes.append(RouteReason.REPEATED_OBJECTIVE_FAILURE)
        environmental = {
            ToolFailureCategory.NETWORK,
            ToolFailureCategory.PERMISSION,
            ToolFailureCategory.RATE_LIMIT,
            ToolFailureCategory.RESOURCE,
        }
        has_reality_conflict = any(
            item.kind.family is SignalFamily.EXECUTION_OBSERVATION
            and isinstance(item.payload, ExecutionOutcomePayload)
            and item.payload.category not in environmental
            for item in attempt
        )
        if (
            attribution.failure_mode is FailureMode.EXECUTION_FAILED
            and terminal.kind.subtype == "success"
            and has_reality_conflict
        ):
            outcomes.append(RouteReason.TOOL_REALITY_CONFLICT)
        return (
            tuple(outcomes),
            bundle.supporting_signal_refs,
            bundle.excluded_ids,
        )

    def _signals(
        self,
        *,
        task_id: str | None = None,
        episode_id: str | None = None,
    ) -> list[CognitiveSignal]:
        return [
            read_signal_from_event_payload(event.payload)
            for _, event in self._store.list_events()
            if event.kind == "cognitive_signal.recorded"
            and (task_id is None or event.task_id == task_id)
            and (episode_id is None or event.episode_id == episode_id)
        ]
