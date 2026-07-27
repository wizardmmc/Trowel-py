from __future__ import annotations

from datetime import datetime, timezone

from tests.model_os.incubation.support import create_command, prepared_running_task
from trowel_py.model_os.default_work import (
    CandidateDraft,
    DefaultWorkRepository,
    GenerationUsage,
    RunDefaultPilotCommand,
    SampledSource,
)
from trowel_py.model_os.incubation import (
    IncubationCandidateDraft,
    IncubationRepository,
    IncubationUsage,
)
from trowel_py.model_os.types import (
    DecisionDisposition,
    DecisionRecord,
    EventEnvelope,
    Provenance,
)
from trowel_py.model_os.waking import WakeConditionKind, WakeObservation


STAMP = "2026-07-27T00:00:00+00:00"


def _event(
    event_id: str,
    kind: str,
    *,
    payload=None,
    provenance=Provenance.MACHINE_OBSERVATION,
    work_item_id="work.scope",
    task_id="task.scope",
    episode_id="episode.scope",
    cause_id=None,
    correlation_id=None,
):
    return EventEnvelope(
        event_id=event_id,
        kind=kind,
        occurred_at=STAMP,
        source="test_observer",
        provenance=provenance,
        policy_version="policy-v1",
        payload=payload or {},
        work_item_id=work_item_id,
        task_id=task_id,
        episode_id=episode_id,
        cause_id=cause_id,
        correlation_id=correlation_id,
    )


def _decision(
    decision_id: str,
    kind: str,
    *,
    choice="keep",
    reason="stable",
    cause_id=None,
    candidates=None,
):
    return DecisionRecord(
        decision_id=decision_id,
        kind=kind,
        disposition=DecisionDisposition.NO_ACTION,
        decided_at=STAMP,
        signals={"refs": []},
        candidates=candidates or [],
        choice=choice,
        reason=reason,
        policy_version="policy-v1",
        work_item_id="work.scope",
        task_id="task.scope",
        episode_id="episode.scope",
        cause_id=cause_id,
    )


def _insert_internal_events(store, *events) -> None:
    with store._tx():
        for event in events:
            store._insert_event_in_tx(event)


def test_task_explain_unifies_six_domains_by_structured_ids(store) -> None:
    store.append_decision(_decision("decision.switch", "attention.schedule"))
    store.append_decision(_decision("decision.route", "cognitive.route"))
    store.append_decision(
        _decision(
            "decision.budget",
            "work_broker.arbitrate",
            choice="deny",
            reason="slot_busy",
        )
    )
    _insert_internal_events(
        store,
        _event(
            "event.wake",
            "wake.consumed",
            payload={"observation_id": "observation.wake"},
        ),
        _event(
            "event.switch",
            "attention.switch_recovery_observed",
            payload={"schedule_decision_id": "decision.switch"},
            cause_id="decision.switch",
        ),
        _event(
            "event.default",
            "default.candidate_observed",
            payload={
                "command_kind": "default.run_pilot",
                "candidate_ids": ["candidate.default"],
            },
            task_id=None,
        ),
        _event(
            "event.incubation",
            "incubation.candidate_observed",
            payload={
                "command_kind": "incubation.run_cycle",
                "candidate_ids": ["candidate.incubation"],
            },
        ),
    )

    report = store.explain_scope("task", "task.scope")

    assert {decision.decision_kind for decision in report.decisions} == {
        "attention.schedule",
        "cognitive.route",
        "work_broker.arbitrate",
    }
    assert {fact.domain for fact in report.facts} >= {
        "wake",
        "switch",
        "default",
        "incubation",
    }
    default = next(fact for fact in report.facts if fact.domain == "default")
    assert default.task_id is None
    assert default.work_item_id == "work.scope"
    assert default.evidence_refs == ("candidate.default",)
    assert report.as_of == store.journal_boundary()


def test_metrics_return_explicit_counts_versions_and_unknown_cost(store) -> None:
    store.append_decision(_decision("decision.attention", "attention.schedule"))
    store.append_decision(_decision("decision.route.metric", "cognitive.route"))
    store.append_decision(
        _decision(
            "decision.broker",
            "work_broker.arbitrate",
            choice="grant",
            reason="granted",
        )
    )
    store.append_decision(
        _decision(
            "decision.usage.known",
            "work_broker.usage",
            choice="recorded",
            reason="usage_observed",
            candidates=[{"role": "usage", "cost": 1.25, "cost_source": "provider_report"}],
        )
    )
    store.append_decision(
        _decision(
            "decision.usage.unknown",
            "work_broker.usage",
            choice="recorded",
            reason="usage_observed",
            candidates=[{"role": "usage", "cost": None, "cost_source": "unknown"}],
        )
    )
    _insert_internal_events(
        store,
        _event("event.episode.closed", "episode.closed"),
        _event(
            "event.route.actual",
            "route.actual_observed",
            payload={"model": "model-fast"},
            cause_id="decision.route.metric",
        ),
        _event(
            "event.default.generated",
            "command.result",
            payload={
                "command_kind": "default.run_pilot",
                "candidate_ids": ["default.1", "default.2"],
                "candidate_count": 2,
            },
        ),
        _event(
            "event.default.outcome",
            "command.result",
            payload={
                "command_kind": "default.candidate_outcome",
                "candidate_id": "default.1",
            },
        ),
        _event(
            "event.incubation.generated",
            "command.result",
            payload={
                "command_kind": "incubation.run_cycle",
                "candidate_ids": ["incubation.1"],
            },
        ),
        _event(
            "event.command.intent",
            "command.intent",
            payload={"command_kind": "example"},
            correlation_id="command.example",
        ),
        _event(
            "event.command.result",
            "command.result",
            payload={"result_code": "succeeded"},
            correlation_id="command.example",
        ),
    )

    report = store.read_metrics(
        window_start="2026-07-26T00:00:00+00:00",
        window_end="2026-07-28T00:00:00+00:00",
    )
    dimensions = {item.name: item for item in report.dimensions}

    assert set(dimensions) == {
        "continuity",
        "scheduling",
        "routing",
        "default",
        "incubation",
        "reliability",
    }
    assert dimensions["default"].ratios[0].numerator == 1
    assert dimensions["default"].ratios[0].denominator == 2
    assert dimensions["default"].ratios[0].unknown == 1
    assert dimensions["incubation"].ratios[0].unknown == 1
    assert dimensions["routing"].model_versions == ("model-fast",)
    assert dimensions["reliability"].cost.known_total == 1.25
    assert dimensions["reliability"].cost.known_count == 1
    assert dimensions["reliability"].cost.unknown_count == 1
    assert dimensions["reliability"].cost.sources == (
        "provider_report",
        "unknown",
    )


def test_fixed_boundary_excludes_later_scope_facts_and_metrics(store) -> None:
    _insert_internal_events(store, _event("event.before", "wake.consumed"))
    boundary = store.journal_boundary()
    _insert_internal_events(store, _event("event.after", "wake.consumed"))

    explanation = store.explain_scope(
        "task",
        "task.scope",
        boundary=boundary,
    )
    metrics = store.read_metrics(
        window_start="2026-07-26T00:00:00+00:00",
        window_end="2026-07-28T00:00:00+00:00",
        boundary=boundary,
    )

    assert [fact.event_id for fact in explanation.facts] == ["event.before"]
    assert metrics.as_of == boundary


def test_explain_keeps_machine_user_verifier_and_model_sources_separate(store) -> None:
    _insert_internal_events(
        store,
        _event("event.machine", "wake.consumed"),
        _event(
            "event.user",
            "default.outcome",
            provenance=Provenance.USER_DECISION,
        ),
        _event(
            "event.model",
            "incubation.proposal",
            provenance=Provenance.MODEL_HYPOTHESIS,
        ),
        _event(
            "event.verifier",
            "cognitive_signal.recorded",
            payload={
                "signal": {
                    "kind": {
                        "identifier": "signal.validator_outcome/pass",
                    }
                }
            },
        ),
    )

    report = store.explain_scope("episode", "episode.scope")

    assert report.source_counts == {
        "machine_observation": 1,
        "user_feedback": 1,
        "verifier": 1,
        "model_report": 1,
        "unknown": 0,
    }


def test_episode_explain_does_not_pull_sibling_episode_by_shared_work_item(
    store,
) -> None:
    _insert_internal_events(
        store,
        _event("event.episode.target", "route.actual_observed"),
        _event(
            "event.episode.sibling",
            "route.actual_observed",
            episode_id="episode.sibling",
        ),
    )

    report = store.explain_scope("episode", "episode.scope")

    assert [fact.event_id for fact in report.facts] == ["event.episode.target"]


def test_domain_metrics_read_real_generation_versions_and_cost_sources(store) -> None:
    now = datetime(2026, 7, 26, 9, 0, tzinfo=timezone.utc)
    default = DefaultWorkRepository(store)
    source = SampledSource(
        uri_at_generation="memory://notes/metric",
        memory_id="memory-metric",
        sampled_content_hash="a" * 64,
        updated="2026-07-26",
        chars=10,
        text="transient private text",
    )
    begun = default.begin(
        RunDefaultPilotCommand(
            "metric-default",
            "codex",
            ("memory://notes/metric",),
        ),
        (source,),
        occurred_at=now,
    )
    default.commit_success(
        begun.generation_id,
        (
            CandidateDraft(
                "candidate",
                ("memory://notes/metric",),
                "question",
                "useful",
                "verify",
                "unknown",
            ),
        ),
        episode_id="episode.metric.default",
        effective_model="model-default",
        usage=GenerationUsage(1, 1, 1.0, 0.1),
        occurred_at=now,
    )

    task, ref = prepared_running_task(store, key="metric-incubation")
    incubation = IncubationRepository(store)
    plan = incubation.create_plan(
        create_command(
            task.task_id,
            ref,
            command_id="metric-incubation-create",
        )
    )
    incubation.consume_wake(
        WakeObservation(
            observation_id=f"manual:{plan.plan_id}",
            kind=WakeConditionKind.MANUAL,
            target_ref=plan.plan_id,
            observed_at=now.isoformat(),
            source="user",
            details={},
        )
    )
    incubation.begin_run(plan.plan_id, occurred_at=now)
    incubation.commit_generation(
        plan.plan_id,
        IncubationCandidateDraft(
            "proposal",
            ("snapshot:prepared",),
            ("new-point",),
            "verify",
            "unknown",
        ),
        effective_model="model-incubation",
        usage=IncubationUsage(1, 1, 1.0, 0.2),
        occurred_at=now,
    )

    report = store.read_metrics(
        window_start="2026-07-26T00:00:00+00:00",
        window_end="2026-07-27T00:00:00+00:00",
    )
    dimensions = {item.name: item for item in report.dimensions}

    assert dimensions["default"].model_versions == ("model-default",)
    assert dimensions["default"].cost.known_total == 0.1
    assert dimensions["default"].cost.sources == ("generation_usage",)
    assert dimensions["incubation"].model_versions == ("model-incubation",)
    assert dimensions["incubation"].cost.known_total == 0.2
    assert dimensions["incubation"].cost.sources == ("generation_usage",)
