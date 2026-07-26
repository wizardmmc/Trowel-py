from __future__ import annotations

import json

from fastapi import FastAPI
from fastapi.testclient import TestClient

from trowel_py.model_os.routes import router


class FakeStarter:
    def __init__(self) -> None:
        self.commands = []

    async def start(self, command):
        self.commands.append(command)
        yield {
            "schema": "agent-event-v1",
            "session_id": "agent-1",
            "runtime": command.runtime,
            "seq": 1,
            "type": "finished",
            "turn_id": "turn-1",
            "item_id": None,
            "payload": {"duration_ms": 1},
        }


def test_start_episode_http_streams_agent_events_and_builds_command() -> None:
    starter = FakeStarter()
    app = FastAPI()
    app.state.model_os_episode_starter = starter
    app.include_router(router, prefix="/api/model-os")
    client = TestClient(app)

    response = client.post(
        "/api/model-os/episodes/start",
        json={
            "work_item_id": "work-1",
            "task_id": None,
            "runtime": "codex",
            "model": "model-1",
            "effort": "high",
            "memory_enabled": False,
            "profile_enabled": True,
            "workdir": "/workspace/project",
            "session_purpose": "default",
            "memory_eligibility": "ineligible",
            "permission": "danger-full-access",
            "idempotency_key": "start-1",
        },
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    payload = json.loads(response.text.removeprefix("data: ").strip())
    assert payload["type"] == "finished"
    command = starter.commands[0]
    assert command.resume_from is None
    assert command.session_purpose.value == "default"
    assert command.memory_eligibility.value == "ineligible"


def test_start_episode_http_is_unavailable_without_kernel_runner() -> None:
    app = FastAPI()
    app.state.model_os_episode_starter = None
    app.include_router(router, prefix="/api/model-os")

    response = TestClient(app).post(
        "/api/model-os/episodes/start",
        json={
            "work_item_id": "work-1",
            "runtime": "codex",
            "memory_enabled": True,
            "profile_enabled": True,
            "workdir": "/workspace/project",
            "session_purpose": "foreground",
            "memory_eligibility": "eligible",
            "permission": "danger-full-access",
            "idempotency_key": "start-1",
        },
    )

    assert response.status_code == 503
