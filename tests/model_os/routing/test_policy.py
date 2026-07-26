from __future__ import annotations

import pytest

from trowel_py.model_os.routing import (
    ROUTE_POLICY_VERSION,
    RouteAction,
    RouteCandidate,
    RouteInput,
    RouteMarker,
    RouteMode,
    RouteReason,
    UserRoutePreference,
    decide_route,
)
from trowel_py.model_os.types import DecisionDisposition
from trowel_py.model_os.work_broker import ModelTier


def _candidate(tier: ModelTier) -> RouteCandidate:
    return RouteCandidate(
        tier=tier,
        model=f"{tier.value}-model",
        effort="low" if tier is ModelTier.FAST else "high",
    )


def _input(**changes) -> RouteInput:
    values = {
        "work_item_id": "work-1",
        "task_id": "task-1",
        "runtime": "codex",
        "mode": RouteMode.SHADOW,
        "user_preference": UserRoutePreference.AUTO,
        "mandatory_markers": (),
        "trusted_pre_route_markers": (),
        "trusted_outcomes": (),
        "previous_tier": None,
        "fixed_model": "fixed-model",
        "fixed_effort": "medium",
        "candidates": (_candidate(ModelTier.FAST), _candidate(ModelTier.DEEP)),
        "input_fact_refs": ("event.task.ready",),
        "evaluation_domain": "coding",
        "canary_approved": False,
    }
    values.update(changes)
    return RouteInput(**values)


def test_shadow_default_proposes_fast_without_changing_fixed_baseline() -> None:
    decision = decide_route(_input())

    assert decision.reason is RouteReason.DEFAULT_FAST
    assert decision.proposed.tier is ModelTier.FAST
    assert (decision.actual.model, decision.actual.effort) == (
        "fixed-model",
        "medium",
    )
    assert decision.disposition is DecisionDisposition.SHADOW
    assert decision.policy_version == ROUTE_POLICY_VERSION


@pytest.mark.parametrize(
    ("preference", "tier", "reason"),
    [
        (UserRoutePreference.FAST, ModelTier.FAST, RouteReason.USER_FAST),
        (UserRoutePreference.DEEP, ModelTier.DEEP, RouteReason.USER_DEEP),
    ],
)
def test_explicit_user_override_executes_even_while_automatic_policy_is_shadowed(
    preference: UserRoutePreference,
    tier: ModelTier,
    reason: RouteReason,
) -> None:
    decision = decide_route(_input(user_preference=preference))

    assert decision.reason is reason
    assert decision.proposed.tier is tier
    assert decision.actual == decision.proposed
    assert decision.disposition is DecisionDisposition.EXECUTE


def test_irreversible_mandatory_policy_denies_conflicting_fast_override() -> None:
    decision = decide_route(
        _input(
            user_preference=UserRoutePreference.FAST,
            mandatory_markers=(RouteMarker.HIGH_IMPACT_IRREVERSIBLE,),
        )
    )

    assert decision.action is RouteAction.DENY
    assert decision.reason is RouteReason.MANDATORY_DEEP
    assert decision.actual is None


def test_trusted_validator_failure_upgrades_next_episode_but_network_does_not() -> None:
    upgraded = decide_route(_input(trusted_outcomes=(RouteReason.VALIDATOR_FAILURE,)))
    ignored = decide_route(_input(trusted_outcomes=()))

    assert upgraded.proposed.tier is ModelTier.DEEP
    assert upgraded.reason is RouteReason.VALIDATOR_FAILURE
    assert ignored.proposed.tier is ModelTier.FAST


def test_deep_failure_stops_instead_of_starting_another_deep_episode() -> None:
    decision = decide_route(
        _input(
            previous_tier=ModelTier.DEEP,
            trusted_outcomes=(RouteReason.VALIDATOR_FAILURE,),
        )
    )

    assert decision.action is RouteAction.DENY
    assert decision.reason is RouteReason.DEEP_FAILURE_STOP


def test_missing_deep_candidate_never_silently_uses_another_runtime() -> None:
    decision = decide_route(
        _input(
            user_preference=UserRoutePreference.DEEP,
            candidates=(_candidate(ModelTier.FAST),),
        )
    )

    assert decision.action is RouteAction.DENY
    assert decision.reason is RouteReason.NO_DEEP_CANDIDATE


def test_router_off_preserves_fixed_model_and_task_identity() -> None:
    route_input = _input(mode=RouteMode.OFF)

    decision = decide_route(route_input)

    assert decision.reason is RouteReason.ROUTER_OFF
    assert decision.actual.model == route_input.fixed_model
    assert decision.actual.effort == route_input.fixed_effort
    assert decision.disposition is DecisionDisposition.NO_ACTION


@pytest.mark.parametrize(
    "reason",
    [
        RouteReason.MATERIAL_USER_CORRECTION,
        RouteReason.REPEATED_OBJECTIVE_FAILURE,
        RouteReason.TOOL_REALITY_CONFLICT,
    ],
)
def test_each_trusted_outcome_reason_proposes_deep(reason: RouteReason) -> None:
    decision = decide_route(_input(trusted_outcomes=(reason,)))

    assert decision.reason is reason
    assert decision.proposed.tier is ModelTier.DEEP


def test_cc_trusted_pre_route_marker_proposes_deep() -> None:
    decision = decide_route(
        _input(
            runtime="claude_code",
            trusted_pre_route_markers=(RouteMarker.EXACT_CONSTRAINT_SEARCH,),
        )
    )

    assert decision.reason is RouteReason.TRUSTED_PRE_ROUTE
    assert decision.proposed.tier is ModelTier.DEEP


def test_mandatory_marker_proposes_deep_without_overriding_shadow() -> None:
    decision = decide_route(
        _input(mandatory_markers=(RouteMarker.HIGH_IMPACT_IRREVERSIBLE,))
    )

    assert decision.reason is RouteReason.MANDATORY_DEEP
    assert decision.proposed.tier is ModelTier.DEEP
    assert decision.actual.model == "fixed-model"


def test_canary_requires_approval_before_automatic_proposal_executes() -> None:
    waiting = decide_route(_input(mode=RouteMode.CANARY, canary_approved=False))
    approved = decide_route(_input(mode=RouteMode.CANARY, canary_approved=True))

    assert waiting.disposition is DecisionDisposition.SHADOW
    assert waiting.actual.model == "fixed-model"
    assert approved.disposition is DecisionDisposition.EXECUTE
    assert approved.actual == approved.proposed


def test_missing_fast_candidate_falls_back_to_fixed_baseline() -> None:
    decision = decide_route(_input(candidates=(_candidate(ModelTier.DEEP),)))

    assert decision.reason is RouteReason.ROUTER_OFF
    assert decision.actual.model == "fixed-model"
