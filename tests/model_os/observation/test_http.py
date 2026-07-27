from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from trowel_py.model_os.journal import JournalBoundary
from trowel_py.model_os.routes import router
from trowel_py.model_os.scheduling import (
    ScheduleCandidate,
    ScheduleInput,
    decide_schedule,
    record_schedule_decision,
)


def _client(store) -> TestClient:
    app = FastAPI()
    app.state.model_os_store = store
    app.include_router(router, prefix="/api/model-os")
    return TestClient(app)


def _record(store):
    decision = decide_schedule(
        ScheduleInput(
            trigger_event_ref="event.http.wake",
            journal_boundary=JournalBoundary(event_seq=5, decision_seq=2),
            candidates=(
                ScheduleCandidate(
                    work_item_id="work.http",
                    task_id="task.http",
                    priority=1,
                    warm_rank=0,
                    created_at="2026-07-27T00:00:00Z",
                    ready_epoch_ref="event.task.ready.http",
                    virtual_service_segments=0,
                ),
            ),
        )
    )
    return record_schedule_decision(store, decision)


def test_journal_page_is_host_neutral_and_returns_fixed_boundary(store) -> None:
    recorded = _record(store)

    response = _client(store).get(
        "/api/model-os/journal",
        params={"task_id": "task.http", "limit": 1},
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["as_of"] == {"event_seq": 1, "decision_seq": 1}
    assert len(data["items"]) == 1
    assert data["items"][0]["entry_id"] == recorded.decision_id
    assert data["items"][0]["stream"] == "decision"
    assert isinstance(data["next_cursor"], str)
    assert "task.http" not in data["next_cursor"]

    second = _client(store).get(
        "/api/model-os/journal",
        params={
            "task_id": "task.http",
            "limit": 1,
            "cursor": data["next_cursor"],
        },
    )
    assert second.json()["data"]["as_of"] == data["as_of"]
    assert second.json()["data"]["items"][0]["kind"] == "command.intent"


def test_explain_and_replay_share_structured_read_contract(store) -> None:
    recorded = _record(store)
    client = _client(store)

    explain = client.get(
        f"/api/model-os/decisions/{recorded.decision_id}/explain"
    )
    replay = client.get(
        f"/api/model-os/decisions/{recorded.decision_id}/replay"
    )

    assert explain.status_code == replay.status_code == 200
    explain_data = explain.json()["data"]
    replay_data = replay.json()["data"]
    assert explain_data["decision_id"] == recorded.decision_id
    assert explain_data["command"]["status"] == "pending"
    assert explain_data["as_of"] == {"event_seq": 1, "decision_seq": 1}
    assert replay_data["status"] == "matched"
    assert replay_data["recorded"] == replay_data["replayed"]
    assert replay_data["input_boundary"] == {
        "event_seq": 5,
        "decision_seq": 2,
    }
    assert replay_data["as_of"] == {"event_seq": 1, "decision_seq": 1}


def test_observation_endpoints_fail_closed_for_bad_cursor_and_missing_decision(
    store,
) -> None:
    client = _client(store)

    bad_cursor = client.get(
        "/api/model-os/journal",
        params={"cursor": "private raw prompt"},
    )
    missing = client.get("/api/model-os/decisions/missing/replay")
    unsafe_policy = client.get(
        "/api/model-os/decisions/missing/replay",
        params={"policy_version": "private raw prompt"},
    )
    unsafe_decision = client.get(
        "/api/model-os/decisions/private%20raw%20prompt/explain"
    )

    assert bad_cursor.status_code == 400
    assert bad_cursor.json()["detail"] == "invalid journal cursor"
    assert missing.status_code == 404
    assert missing.json()["detail"] == "decision not found"
    assert unsafe_policy.status_code == 422
    assert "private raw prompt" not in unsafe_policy.text
    assert unsafe_decision.status_code == 422
    assert "private raw prompt" not in unsafe_decision.text


def test_scope_explain_and_metrics_are_directly_consumable(store) -> None:
    _record(store)
    client = _client(store)

    task = client.get("/api/model-os/tasks/task.http/explain")
    metrics = client.get(
        "/api/model-os/metrics",
        params={
            "window_start": "2026-07-26T00:00:00+00:00",
            "window_end": "2026-07-28T00:00:00+00:00",
        },
    )

    assert task.status_code == metrics.status_code == 200
    assert task.json()["data"]["subject_id"] == "task.http"
    assert task.json()["data"]["decisions"][0]["decision_kind"] == "attention.schedule"
    assert [item["name"] for item in metrics.json()["data"]["dimensions"]] == [
        "continuity",
        "scheduling",
        "routing",
        "default",
        "incubation",
        "reliability",
    ]


def test_scope_and_metrics_errors_do_not_echo_attacker_input(store) -> None:
    client = _client(store)
    subject = client.get("/api/model-os/tasks/private%20prompt/explain")
    window = client.get(
        "/api/model-os/metrics",
        params={"window_start": "private raw prompt", "window_end": "also private"},
    )

    assert subject.status_code == window.status_code == 422
    assert "private prompt" not in subject.text
    assert "private raw prompt" not in window.text
