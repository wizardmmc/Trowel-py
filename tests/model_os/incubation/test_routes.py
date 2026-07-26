from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from trowel_py.model_os.incubation import IncubationRepository
from trowel_py.model_os.routes import router
from trowel_py.model_os.waking.controller import WakeController

from tests.model_os.incubation.support import prepared_running_task


class RouteService:
    def __init__(self, repository) -> None:
        self.repository = repository
        self.wakes = []

    def create_plan(self, command):
        return self.repository.create_plan(command)

    def trigger(self, wake):
        self.wakes.append(wake)
        return True

    def cleanup_artifacts(self, **kwargs):
        return {
            "cleaned_candidates": self.repository.cleanup_artifacts(**kwargs),
            "recovered_expired_leases": 0,
        }


def _client(store):
    repository = IncubationRepository(store)
    service = RouteService(repository)
    app = FastAPI()
    app.include_router(router)
    app.state.model_os_incubation = service
    app.state.model_os_wake_controller = WakeController(
        store, observation_consumers=(repository.consume_wake,)
    )
    app.state.model_os_attention_scheduler = None
    return TestClient(app), service


def _plan_body(task, ref) -> dict:
    return {
        "command_id": "incubation-http-invalid",
        "task_id": task.task_id,
        "prepared_snapshot_ref": {
            "episode_id": ref.episode_id,
            "version": ref.version,
            "committed_event_id": ref.committed_event_id,
            "payload_hash": ref.payload_hash,
        },
        "unresolved_question": "怎样验证约束？",
        "wake_condition": {
            "kind": "manual",
            "target_ref": task.task_id,
            "match_params": {},
        },
        "deadline": None,
        "budget": {"calls": 1},
        "runtime": "codex",
    }


def test_http_create_read_early_wake_and_cancel_plan(store) -> None:
    task, ref = prepared_running_task(store)
    client, service = _client(store)
    created = client.post(
        "/incubation/plans",
        json={
            "command_id": "incubation-http-1",
            "task_id": task.task_id,
            "prepared_snapshot_ref": {
                "episode_id": ref.episode_id,
                "version": ref.version,
                "committed_event_id": ref.committed_event_id,
                "payload_hash": ref.payload_hash,
            },
            "unresolved_question": "重启后怎样确认 child 是否已经执行？",
            "wake_condition": {
                "kind": "time",
                "target_ref": "clock",
                "match_params": {},
                "due_at": "2026-07-27T08:00:00+00:00",
            },
            "deadline": "2026-07-28T08:00:00+00:00",
            "budget": {"calls": 1},
            "runtime": "codex",
        },
    )

    assert created.status_code == 200
    plan = created.json()["data"]
    assert plan["status"] == "pending_wake"
    read = client.get(f"/incubation/plans/{plan['plan_id']}")
    assert read.status_code == 200
    assert read.json()["data"] == plan

    woke = client.post(
        f"/incubation/plans/{plan['plan_id']}/wake",
        json={"observation_id": "early-http-1"},
    )
    assert woke.status_code == 200
    assert woke.json()["data"]["status"] == "ready"
    assert len(service.wakes) == 1

    cancelled = client.post(
        f"/incubation/plans/{plan['plan_id']}/cancel",
        json={"command_id": "cancel-http-1"},
    )
    assert cancelled.status_code == 200
    assert cancelled.json()["data"]["stop_reason"] == "user_cancelled"


def test_http_rejects_budget_axes_that_v0_cannot_enforce(store) -> None:
    task, ref = prepared_running_task(store)
    client, _ = _client(store)

    response = client.post(
        "/incubation/plans",
        json={
            "command_id": "incubation-unsupported-budget",
            "task_id": task.task_id,
            "prepared_snapshot_ref": {
                "episode_id": ref.episode_id,
                "version": ref.version,
                "committed_event_id": ref.committed_event_id,
                "payload_hash": ref.payload_hash,
            },
            "unresolved_question": "怎样验证约束？",
            "wake_condition": {
                "kind": "manual",
                "target_ref": task.task_id,
                "match_params": {},
            },
            "deadline": None,
            "budget": {"calls": 1, "tokens": 100},
            "runtime": "codex",
        },
    )

    assert response.status_code == 422


def test_http_returns_stable_reason_for_missing_unresolved_question(store) -> None:
    task, ref = prepared_running_task(store)
    client, _ = _client(store)
    body = _plan_body(task, ref)
    body["unresolved_question"] = "   "

    response = client.post("/incubation/plans", json=body)

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "unresolved_question_missing"


def test_http_returns_stable_reason_for_incomplete_wake_condition(store) -> None:
    task, ref = prepared_running_task(store)
    client, _ = _client(store)
    body = _plan_body(task, ref)
    body["wake_condition"] = {
        "kind": "time",
        "target_ref": "clock",
        "match_params": {},
    }

    response = client.post("/incubation/plans", json=body)

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "wake_condition_missing"


def test_http_returns_stable_reason_for_missing_snapshot(store) -> None:
    task, ref = prepared_running_task(store)
    client, _ = _client(store)
    body = _plan_body(task, ref)
    body.pop("prepared_snapshot_ref")

    response = client.post("/incubation/plans", json=body)

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "snapshot_missing"


def test_http_returns_stable_reason_for_incomplete_snapshot(store) -> None:
    task, ref = prepared_running_task(store)
    client, _ = _client(store)
    body = _plan_body(task, ref)
    body["prepared_snapshot_ref"].pop("payload_hash")

    response = client.post("/incubation/plans", json=body)

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "snapshot_missing"


@pytest.mark.parametrize("budget", [{}, {"calls": 0}])
def test_http_returns_stable_reason_for_missing_or_denied_call_budget(
    store, budget
) -> None:
    task, ref = prepared_running_task(store)
    client, _ = _client(store)
    body = _plan_body(task, ref)
    body["budget"] = budget

    response = client.post("/incubation/plans", json=body)

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "budget_denied"


def test_http_returns_stable_reason_for_missing_wake_kind(store) -> None:
    task, ref = prepared_running_task(store)
    client, _ = _client(store)
    body = _plan_body(task, ref)
    body["wake_condition"].pop("kind")

    response = client.post("/incubation/plans", json=body)

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "wake_condition_missing"


def test_http_returns_stable_reason_for_missing_budget(store) -> None:
    task, ref = prepared_running_task(store)
    client, _ = _client(store)
    body = _plan_body(task, ref)
    body["budget"] = {}

    response = client.post("/incubation/plans", json=body)

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "budget_denied"
