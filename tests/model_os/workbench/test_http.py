from __future__ import annotations

from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from trowel_py.model_os.routes import _workbench_sse_frame, router


def _client(store) -> TestClient:
    app = FastAPI()
    app.state.model_os_store = store
    app.include_router(router, prefix="/api/model-os")
    return TestClient(app)


def test_workbench_snapshot_and_user_commands_round_trip_through_store(store) -> None:
    task = store.create_task_from_user_request(
        original_goal="实现 Model OS 工作台",
        idempotency_key="create-workbench",
    )
    client = _client(store)

    initial = client.get("/api/model-os/workbench")
    assert initial.status_code == 200
    assert initial.json()["data"]["tasks"][0]["goal"] == "实现 Model OS 工作台"

    warmed = client.post(
        f"/api/model-os/tasks/{task.task_id}/warm",
        json={"warm": True},
    )
    assert warmed.status_code == 200
    task_view = next(
        item
        for item in warmed.json()["data"]["tasks"]
        if item["task_id"] == task.task_id
    )
    assert task_view["warm"] is True

    paused = client.post(
        "/api/model-os/automation",
        json={"paused": True, "idempotency_key": "pause-http"},
    )
    assert paused.status_code == 200
    assert paused.json()["data"]["automation_paused"] is True

    refreshed = client.get("/api/model-os/workbench").json()["data"]
    assert refreshed["automation_paused"] is True


def test_workbench_event_frame_is_a_complete_sse_envelope() -> None:
    frame = _workbench_sse_frame(
        {
            "as_of": {"event_seq": 3, "decision_seq": 1},
            "tasks": [{"goal": "实现工作台"}],
        }
    )

    assert frame.startswith(b"data: ")
    assert frame.endswith(b"\n\n")
    assert '"success": true' in frame.decode()
    assert "实现工作台" in frame.decode()


def test_waiting_reply_is_bound_to_original_task_session_and_request() -> None:
    triggered: list[str] = []

    class Store:
        def read_snapshot(self):
            return SimpleNamespace(
                tasks=(
                    SimpleNamespace(
                        task_id="task-1",
                        waiting_condition=SimpleNamespace(
                            correlation_id="request-1",
                            episode_id="episode-1",
                        ),
                    ),
                )
            )

        def episode_runtime_binding(self, episode_id):
            assert episode_id == "episode-1"
            return SimpleNamespace(agent_session_id="session-1")

    class Hub:
        def pending_request(self, session_id, correlation_id):
            assert (session_id, correlation_id) == ("session-1", "request-1")
            return {
                "kind": "input",
                "questions": [{"question": "是否继续实现？"}],
                "available_decisions": [],
            }

        def runtime_generation(self, session_id):
            assert session_id == "session-1"
            return "generation-1"

    class Wake:
        def queue_for_session(self, session_id, **kwargs):
            assert session_id == "session-1"
            assert kwargs == {
                "correlation_id": "request-1",
                "runtime_generation": "generation-1",
                "payload": {
                    "cancel": False,
                    "answers": {"是否继续实现？": "继续实现"},
                },
            }
            return SimpleNamespace(wake_id="wake-1", episode_id="episode-1")

    class Scheduler:
        async def trigger(self, wake_id):
            triggered.append(wake_id)

    app = FastAPI()
    app.state.model_os_store = Store()
    app.state.agent_hub = Hub()
    app.state.model_os_wake_controller = Wake()
    app.state.model_os_attention_scheduler = Scheduler()
    app.include_router(router, prefix="/api/model-os")
    response = TestClient(app).post(
        "/api/model-os/workbench/tasks/task-1/reply",
        json={
            "correlation_id": "request-1",
            "answers": {"是否继续实现？": "继续实现"},
        },
    )

    assert response.status_code == 200
    assert response.json()["data"] == {
        "queued": True,
        "episode_id": "episode-1",
    }
    assert triggered == ["wake-1"]


def test_instruction_resolves_session_from_current_foreground_task() -> None:
    sent: list[tuple[str, str]] = []

    class Store:
        def read_snapshot(self):
            return SimpleNamespace(
                foreground_task_id="task-1",
                episodes=(
                    SimpleNamespace(
                        episode_id="episode-1",
                        task_id="task-1",
                        status=SimpleNamespace(is_terminal=False),
                        updated_at="2026-07-27T02:00:00+00:00",
                    ),
                ),
            )

        def episode_runtime_binding(self, episode_id):
            assert episode_id == "episode-1"
            return SimpleNamespace(
                agent_session_id="session-authoritative",
                possible_orphan=False,
            )

    class Hub:
        async def stream(self, session_id, text):
            sent.append((session_id, text))
            yield {"type": "finished"}

        def error_envelope(self, session_id, message):
            return {"type": "error", "session_id": session_id, "message": message}

    app = FastAPI()
    app.state.model_os_store = Store()
    app.state.agent_hub = Hub()
    app.include_router(router, prefix="/api/model-os")
    client = TestClient(app)

    response = client.post(
        "/api/model-os/workbench/instruction",
        json={"task_id": "task-1", "text": "继续完成视觉核对"},
    )

    assert response.status_code == 200
    assert '"type": "finished"' in response.text
    assert sent == [("session-authoritative", "继续完成视觉核对")]

    stale = client.post(
        "/api/model-os/workbench/instruction",
        json={"task_id": "task-stale", "text": "发给旧任务"},
    )
    assert stale.status_code == 409
    assert sent == [("session-authoritative", "继续完成视觉核对")]
