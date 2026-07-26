"""不访问外部状态的 Router Policy v0。"""

from __future__ import annotations

from trowel_py.model_os.routing.models import (
    RouteAction,
    RouteCandidate,
    RouteConfidenceSource,
    RouteDecision,
    RouteInput,
    RouteMarker,
    RouteMode,
    RouteReason,
    UserRoutePreference,
)
from trowel_py.model_os.types import DecisionDisposition
from trowel_py.model_os.work_broker import ModelTier

_OUTCOME_ORDER = (
    RouteReason.VALIDATOR_FAILURE,
    RouteReason.MATERIAL_USER_CORRECTION,
    RouteReason.REPEATED_OBJECTIVE_FAILURE,
    RouteReason.TOOL_REALITY_CONFLICT,
)


def _candidate(route_input: RouteInput, tier: ModelTier) -> RouteCandidate | None:
    return next((item for item in route_input.candidates if item.tier is tier), None)


def _baseline(route_input: RouteInput) -> RouteCandidate:
    configured = next(
        (
            item
            for item in route_input.candidates
            if (item.request_model or item.model) == route_input.fixed_model
            and item.effort == route_input.fixed_effort
        ),
        None,
    )
    if configured is not None:
        return configured
    return RouteCandidate(None, route_input.fixed_model, route_input.fixed_effort)


def _deny(
    route_input: RouteInput,
    reason: RouteReason,
    confidence: RouteConfidenceSource,
    *,
    proposed: RouteCandidate | None = None,
) -> RouteDecision:
    return RouteDecision(
        action=RouteAction.DENY,
        reason=reason,
        confidence_source=confidence,
        proposed=proposed or _baseline(route_input),
        actual=None,
        disposition=DecisionDisposition.NO_ACTION,
        supporting_signal_refs=route_input.input_fact_refs,
    )


def _use(
    route_input: RouteInput,
    candidate: RouteCandidate,
    reason: RouteReason,
    confidence: RouteConfidenceSource,
) -> RouteDecision:
    explicit_user = route_input.user_preference is not UserRoutePreference.AUTO
    execute = explicit_user or (
        route_input.mode is RouteMode.CANARY and route_input.canary_approved
    )
    actual = candidate if execute else _baseline(route_input)
    return RouteDecision(
        action=RouteAction.USE,
        reason=reason,
        confidence_source=confidence,
        proposed=candidate,
        actual=actual,
        disposition=(
            DecisionDisposition.EXECUTE if execute else DecisionDisposition.SHADOW
        ),
        supporting_signal_refs=route_input.input_fact_refs,
    )


def decide_route(route_input: RouteInput) -> RouteDecision:
    baseline = _baseline(route_input)
    if route_input.mode is RouteMode.OFF:
        return RouteDecision(
            action=RouteAction.USE,
            reason=RouteReason.ROUTER_OFF,
            confidence_source=RouteConfidenceSource.FIXED_BASELINE,
            proposed=baseline,
            actual=baseline,
            disposition=DecisionDisposition.NO_ACTION,
            supporting_signal_refs=route_input.input_fact_refs,
        )

    mandatory = RouteMarker.HIGH_IMPACT_IRREVERSIBLE in route_input.mandatory_markers
    if mandatory and route_input.user_preference is UserRoutePreference.FAST:
        return _deny(
            route_input,
            RouteReason.MANDATORY_DEEP,
            RouteConfidenceSource.MANDATORY_POLICY,
            proposed=_candidate(route_input, ModelTier.DEEP),
        )

    if route_input.user_preference is UserRoutePreference.DEEP or mandatory:
        deep = _candidate(route_input, ModelTier.DEEP)
        if deep is None:
            return _deny(
                route_input,
                RouteReason.NO_DEEP_CANDIDATE,
                RouteConfidenceSource.USER
                if route_input.user_preference is UserRoutePreference.DEEP
                else RouteConfidenceSource.MANDATORY_POLICY,
            )
        return _use(
            route_input,
            deep,
            RouteReason.USER_DEEP
            if route_input.user_preference is UserRoutePreference.DEEP
            else RouteReason.MANDATORY_DEEP,
            RouteConfidenceSource.USER
            if route_input.user_preference is UserRoutePreference.DEEP
            else RouteConfidenceSource.MANDATORY_POLICY,
        )

    if route_input.user_preference is UserRoutePreference.FAST:
        fast = _candidate(route_input, ModelTier.FAST)
        if fast is None:
            return RouteDecision(
                action=RouteAction.USE,
                reason=RouteReason.ROUTER_OFF,
                confidence_source=RouteConfidenceSource.FIXED_BASELINE,
                proposed=baseline,
                actual=baseline,
                disposition=DecisionDisposition.NO_ACTION,
                supporting_signal_refs=route_input.input_fact_refs,
            )
        return _use(
            route_input,
            fast,
            RouteReason.USER_FAST,
            RouteConfidenceSource.USER,
        )

    trusted_outcome = next(
        (item for item in _OUTCOME_ORDER if item in route_input.trusted_outcomes),
        None,
    )
    if trusted_outcome is not None and route_input.previous_tier is ModelTier.DEEP:
        return _deny(
            route_input,
            RouteReason.DEEP_FAILURE_STOP,
            RouteConfidenceSource.TRUSTED_OUTCOME,
        )

    supported_pre_route = {
        RouteMarker.EXACT_CONSTRAINT_SEARCH,
        RouteMarker.MULTI_SCENARIO_CONTINGENCY,
    }
    if route_input.runtime in {"cc", "claude_code"} and any(
        item in supported_pre_route for item in route_input.trusted_pre_route_markers
    ):
        deep = _candidate(route_input, ModelTier.DEEP)
        if deep is None:
            return _deny(
                route_input,
                RouteReason.NO_DEEP_CANDIDATE,
                RouteConfidenceSource.PAIRED_POLICY,
            )
        return _use(
            route_input,
            deep,
            RouteReason.TRUSTED_PRE_ROUTE,
            RouteConfidenceSource.PAIRED_POLICY,
        )

    if trusted_outcome is not None:
        deep = _candidate(route_input, ModelTier.DEEP)
        if deep is None:
            return _deny(
                route_input,
                RouteReason.NO_DEEP_CANDIDATE,
                RouteConfidenceSource.TRUSTED_OUTCOME,
            )
        return _use(
            route_input,
            deep,
            trusted_outcome,
            RouteConfidenceSource.TRUSTED_OUTCOME,
        )

    fast = _candidate(route_input, ModelTier.FAST)
    if fast is None:
        return RouteDecision(
            action=RouteAction.USE,
            reason=RouteReason.ROUTER_OFF,
            confidence_source=RouteConfidenceSource.FIXED_BASELINE,
            proposed=baseline,
            actual=baseline,
            disposition=DecisionDisposition.NO_ACTION,
            supporting_signal_refs=route_input.input_fact_refs,
        )
    return _use(
        route_input,
        fast,
        RouteReason.DEFAULT_FAST,
        RouteConfidenceSource.PAIRED_POLICY,
    )
