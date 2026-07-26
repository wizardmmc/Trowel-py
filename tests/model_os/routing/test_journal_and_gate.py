from __future__ import annotations

from dataclasses import replace

import pytest

from trowel_py.model_os.journal import JournalIdentityConflict
from trowel_py.model_os.routing import (
    RouteCandidate,
    RouteInput,
    RouteMode,
    RouteReviewClass,
    UserRoutePreference,
    build_route_gate,
    decide_route,
    record_route_actual,
    record_route_approval,
    record_route_decision,
    record_route_review,
)
from trowel_py.model_os.work_broker import ModelTier
from trowel_py.model_os.types import EventEnvelope, EventKind, Provenance


def _input(index: int, *, domain: str, preference: UserRoutePreference) -> RouteInput:
    return RouteInput(
        work_item_id=f"work-{index}",
        task_id=f"task-{index}",
        runtime="codex",
        mode=RouteMode.SHADOW,
        user_preference=preference,
        mandatory_markers=(),
        trusted_pre_route_markers=(),
        trusted_outcomes=(),
        previous_tier=None,
        fixed_model="fixed-model",
        fixed_effort="medium",
        candidates=(
            RouteCandidate(ModelTier.FAST, "fast-model", "low"),
            RouteCandidate(ModelTier.DEEP, "deep-model", "high"),
        ),
        input_fact_refs=(f"event.input.{index}",),
        evaluation_domain=domain,
        canary_approved=False,
    )


def _record_terminal(
    store, index: int, route_input: RouteInput, *, episode_id: str | None = None
) -> None:
    store.append_event(
        EventEnvelope(
            event_id=f"event.episode.start.result.synthetic.{index}",
            kind=EventKind.COMMAND_RESULT,
            occurred_at="2026-07-26T00:00:01Z",
            source="episode_runner",
            provenance=Provenance.MACHINE_OBSERVATION,
            policy_version="episode-start-v1",
            payload={"result_code": "terminal_observed", "evidence_refs": []},
            work_item_id=route_input.work_item_id,
            task_id=route_input.task_id,
            episode_id=episode_id or f"episode-{index}",
            cause_id=f"decision.episode.start.synthetic.{index}",
            correlation_id=f"command.episode.start.synthetic.{index}",
        )
    )


def test_route_decision_is_idempotent_and_keeps_proposed_and_actual(store) -> None:
    route_input = _input(1, domain="coding", preference=UserRoutePreference.AUTO)
    decision = decide_route(route_input)

    first = record_route_decision(store, "route-key-1", route_input, decision)
    replay = record_route_decision(store, "route-key-1", route_input, decision)

    assert first.decision_id == replay.decision_id
    row = next(
        item
        for _, item in store.list_decisions()
        if item.decision_id == first.decision_id
    )
    assert row.kind == "cognitive.route"
    assert row.choice == "fast"
    candidates = {item["role"]: item for item in row.candidates}
    assert candidates["proposed"]["model"] == "fast-model"
    assert candidates["actual"]["model"] == "fixed-model"


def test_route_decision_rejects_same_key_with_different_input(store) -> None:
    first = _input(1, domain="coding", preference=UserRoutePreference.AUTO)
    conflicting = _input(2, domain="research", preference=UserRoutePreference.DEEP)
    record_route_decision(store, "same-route-key", first, decide_route(first))

    with pytest.raises(JournalIdentityConflict):
        record_route_decision(
            store,
            "same-route-key",
            conflicting,
            decide_route(conflicting),
        )


def test_gate_uses_real_actual_rows_and_keeps_unknown_in_denominator(store) -> None:
    domains = ("coding", "research", "life")
    for index in range(30):
        preference = UserRoutePreference.DEEP if index < 3 else UserRoutePreference.AUTO
        route_input = _input(index, domain=domains[index % 3], preference=preference)
        recorded = record_route_decision(
            store,
            f"route-key-{index}",
            route_input,
            decide_route(route_input),
        )
        record_route_actual(
            store,
            recorded.decision_id,
            episode_id=f"episode-{index}",
            model="deep-model" if index < 3 else "fixed-model",
            effort="high" if index < 3 else "medium",
            tier=ModelTier.DEEP if index < 3 else None,
            evidence_ref=f"binding-{index}",
        )
        _record_terminal(store, index, route_input)
        if index < 10:
            # Gate 算术测试直接构造已通过写入门禁的 journal 事实；
            # record_route_review 的 authority 拒绝由独立测试覆盖。
            store.append_event(
                EventEnvelope(
                    event_id=f"event.route.review.synthetic.{index}",
                    kind="route.review_recorded",
                    occurred_at="2026-07-26T00:00:00Z",
                    source="test_projection",
                    provenance=Provenance.USER_DECISION,
                    policy_version="m8-l10-paired-20260723",
                    payload={
                        "classification": "correct",
                        "trusted_verifier": True,
                        "evidence_refs": [f"signal.validator.{index}"],
                        "reviewer_ref_hash": f"sha256:reviewer{index:02d}",
                    },
                    work_item_id=route_input.work_item_id,
                    task_id=route_input.task_id,
                    cause_id=recorded.decision_id,
                    correlation_id=recorded.correlation_id,
                )
            )
        elif index == 10:
            record_route_review(
                store,
                recorded.decision_id,
                classification=RouteReviewClass.MISSED_DEEP_NEED,
                trusted_verifier=False,
                evidence_refs=("review.manual.10",),
                reviewer_ref="reviewer-10",
            )

    gate = build_route_gate(store)

    assert gate.live_episodes == 30
    assert gate.reviewed_episodes == 11
    assert gate.domains == ("coding", "life", "research")
    assert gate.trusted_verifier_episodes == 10
    assert gate.missed_deep_need == 1
    assert gate.unjustified_deep == 0
    assert gate.review_unknown == 19
    assert gate.user_override_total == 3
    assert gate.user_override_executed == 3
    assert gate.actual_match_executed == gate.actual_match_total == 30
    assert gate.ready_for_human_review is True
    assert gate.canary_approved is False

    record_route_approval(store, reviewer_ref="human-approver")
    assert build_route_gate(store).canary_approved is True

    regressed_input = _input(31, domain="coding", preference=UserRoutePreference.DEEP)
    regressed = record_route_decision(
        store,
        "route-key-regressed",
        regressed_input,
        decide_route(regressed_input),
    )
    record_route_actual(
        store,
        regressed.decision_id,
        episode_id="episode-regressed",
        model="wrong-effective-model",
        effort="high",
        tier=ModelTier.DEEP,
        evidence_ref="binding-regressed",
    )
    _record_terminal(store, 31, regressed_input, episode_id="episode-regressed")
    regressed_gate = build_route_gate(store)
    assert regressed_gate.ready_for_human_review is False
    assert regressed_gate.canary_approved is False


def test_gate_rejects_effective_model_mismatch(store) -> None:
    route_input = _input(50, domain="coding", preference=UserRoutePreference.DEEP)
    recorded = record_route_decision(
        store, "route-key-mismatch", route_input, decide_route(route_input)
    )
    record_route_actual(
        store,
        recorded.decision_id,
        episode_id="episode-mismatch",
        model="runtime-adjusted-model",
        effort="medium",
        tier=ModelTier.DEEP,
        evidence_ref="binding-mismatch",
    )
    assert build_route_gate(store).live_episodes == 0
    _record_terminal(store, 50, route_input, episode_id="episode-mismatch")

    gate = build_route_gate(store)

    assert gate.actual_match_total == 1
    assert gate.actual_match_executed == 0
    assert gate.ready_for_human_review is False


def test_gate_does_not_count_router_off_episode_as_shadow(store) -> None:
    route_input = _input(60, domain="coding", preference=UserRoutePreference.AUTO)
    route_input = replace(route_input, mode=RouteMode.OFF)
    recorded = record_route_decision(
        store, "route-key-off", route_input, decide_route(route_input)
    )
    record_route_actual(
        store,
        recorded.decision_id,
        episode_id="episode-off",
        model="fixed-model",
        effort="medium",
        tier=None,
        evidence_ref="binding-off",
    )
    _record_terminal(store, 60, route_input, episode_id="episode-off")

    assert build_route_gate(store).live_episodes == 0


def test_trusted_review_rejects_unverified_evidence(store) -> None:
    route_input = _input(40, domain="coding", preference=UserRoutePreference.AUTO)
    recorded = record_route_decision(
        store, "route-key-untrusted", route_input, decide_route(route_input)
    )
    record_route_actual(
        store,
        recorded.decision_id,
        episode_id="episode-untrusted",
        model="fixed-model",
        effort="medium",
        tier=None,
        evidence_ref="binding-untrusted",
    )
    _record_terminal(store, 40, route_input, episode_id="episode-untrusted")

    with pytest.raises(ValueError, match="validator evidence"):
        record_route_review(
            store,
            recorded.decision_id,
            classification=RouteReviewClass.CORRECT,
            trusted_verifier=True,
            evidence_refs=("signal.does-not-exist",),
            reviewer_ref="reviewer",
        )
