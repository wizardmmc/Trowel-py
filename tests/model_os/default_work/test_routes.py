from __future__ import annotations

from datetime import timezone

from fastapi import FastAPI
from fastapi.testclient import TestClient

from trowel_py.model_os.default_work import (
    Candidate,
    CandidateStatus,
    DefaultWorkError,
    GateReport,
    PilotResult,
)
from trowel_py.model_os.routes import router


def _candidate(status: CandidateStatus = CandidateStatus.SHOWN) -> Candidate:
    return Candidate(
        candidate_id="candidate-1",
        generation_id="generation-1",
        content="A useful link",
        source_refs=("memory://notes/a",),
        related_question="question",
        why_useful="useful",
        verification="verify",
        uncertainty="uncertain",
        runtime="codex",
        effective_model="deep-model",
        tier="deep",
        policy_version="policy",
        status=status,
        created_at="2026-07-26T00:00:00+00:00",
        shown_at="2026-07-26T00:00:00+00:00",
    )


class Repository:
    def record_outcome(self, **kwargs):
        self.outcome = kwargs
        return _candidate(CandidateStatus(kwargs["outcome"]))

    def gate_report(self):
        return GateReport(
            status="insufficient_outcomes",
            outcome_count=0,
            adopted=0,
            invalid=0,
            dismissed=0,
            adoption_rate=None,
            invalid_rate=None,
            total_input_tokens=0,
            total_output_tokens=0,
            known_cost=None,
            unknown_cost_generations=0,
        )

    def generation_view(self, generation_id):
        return {
            "generation_id": generation_id,
            "sources": [
                {
                    "uri_at_generation": "memory://notes/a",
                    "memory_id": "stable-a",
                    "sampled_content_hash": "a" * 64,
                    "updated": "2026-07-26",
                    "chars": 20,
                }
            ],
            "usage": {
                "input_tokens": 10,
                "output_tokens": 5,
                "wall_seconds": 1.5,
                "cost": None,
            },
        }


class Service:
    def __init__(self) -> None:
        self.repository = Repository()

    async def run(self, command):
        self.command = command
        if not 1 <= len(command.source_refs) <= 3:
            raise DefaultWorkError("invalid_source_count")
        candidate = _candidate()
        return PilotResult("work-1", "episode-1", "generation-1", (candidate,))


def _client():
    app = FastAPI()
    app.include_router(router, prefix="/api/model-os")
    service = Service()
    app.state.model_os_default_work = service
    return TestClient(app), service


def test_pilot_returns_full_shown_candidates() -> None:
    client, service = _client()
    response = client.post(
        "/api/model-os/default/pilot",
        json={
            "command_id": "command-1",
            "runtime": "codex",
            "source_refs": ["memory://notes/a"],
        },
    )
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["candidate_ids"] == ["candidate-1"]
    assert data["candidates"][0]["status"] == "shown"
    assert service.command.runtime == "codex"


def test_source_count_business_rejection_uses_stable_code() -> None:
    client, _ = _client()
    response = client.post(
        "/api/model-os/default/pilot",
        json={"command_id": "command-1", "runtime": "codex", "source_refs": []},
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "invalid_source_count"


def test_outcome_and_gate_endpoints_expose_manual_only_state() -> None:
    client, service = _client()
    outcome = client.post(
        "/api/model-os/default/candidates/candidate-1/outcome",
        json={"command_id": "outcome-1", "outcome": "invalid", "reason": "wrong"},
    )
    assert outcome.status_code == 200
    assert outcome.json()["data"]["status"] == "invalid"
    assert service.repository.outcome["occurred_at"].tzinfo is timezone.utc

    gate = client.get("/api/model-os/default/gate").json()["data"]
    assert gate["status"] == "insufficient_outcomes"
    assert gate["automatic_default"] is False

    generation = client.get("/api/model-os/default/generations/generation-1").json()[
        "data"
    ]
    assert generation["sources"][0]["memory_id"] == "stable-a"
    assert generation["usage"]["cost"] is None
