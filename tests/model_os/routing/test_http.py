from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from trowel_py.model_os.routes import router
from trowel_py.model_os.routing import (
    RouteCandidate,
    RouteInput,
    RouteMode,
    UserRoutePreference,
    decide_route,
    record_route_actual,
    record_route_decision,
)
from trowel_py.model_os.work_broker import ModelTier
from trowel_py.model_os.types import EventEnvelope, EventKind, Provenance


def _client(store) -> TestClient:
    app = FastAPI()
    app.state.model_os_store = store
    app.include_router(router, prefix="/api/model-os")
    return TestClient(app)


def _route(store):
    route_input = RouteInput(
        work_item_id="work-http",
        task_id=None,
        runtime="codex",
        mode=RouteMode.SHADOW,
        user_preference=UserRoutePreference.AUTO,
        mandatory_markers=(),
        trusted_pre_route_markers=(),
        trusted_outcomes=(),
        previous_tier=None,
        fixed_model="fixed-model",
        fixed_effort="medium",
        candidates=(RouteCandidate(ModelTier.FAST, "fast-model", "low"),),
        input_fact_refs=("decision.input.http",),
        evaluation_domain="coding",
        canary_approved=False,
    )
    return record_route_decision(
        store, "route-http", route_input, decide_route(route_input)
    )


def test_route_gate_exposes_fixed_boundary_and_denominators(store) -> None:
    response = _client(store).get("/api/model-os/routing/gate")

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["live_episodes"] == 0
    assert data["reviewed_episodes"] == 0
    assert data["actual_match_total"] == 0
    assert data["as_of"] == {"event_seq": 0, "decision_seq": 0}


def test_route_review_is_append_only_and_approval_fails_before_gate(store) -> None:
    recorded = _route(store)
    record_route_actual(
        store,
        recorded.decision_id,
        episode_id="episode-http",
        model="fixed-model",
        effort="medium",
        tier=None,
        evidence_ref="binding-http",
    )
    store.append_event(
        EventEnvelope(
            event_id="event.episode.start.result.http",
            kind=EventKind.COMMAND_RESULT,
            occurred_at="2026-07-26T00:00:01Z",
            source="episode_runner",
            provenance=Provenance.MACHINE_OBSERVATION,
            policy_version="episode-start-v1",
            payload={"result_code": "terminal_observed", "evidence_refs": []},
            work_item_id="work-http",
            episode_id="episode-http",
            cause_id="decision.episode.start.http",
            correlation_id="command.episode.start.http",
        )
    )
    client = _client(store)

    review = client.post(
        f"/api/model-os/routing/decisions/{recorded.decision_id}/review",
        json={
            "classification": "unknown",
            "trusted_verifier": False,
            "evidence_refs": ["review.manual.http"],
            "reviewer_ref": "human-reviewer",
        },
    )
    approval = client.post(
        "/api/model-os/routing/approval",
        json={"reviewer_ref": "human-reviewer"},
    )

    assert review.status_code == 200
    assert approval.status_code == 409
