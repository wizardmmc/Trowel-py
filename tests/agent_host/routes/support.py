import json
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from trowel_py.agent_host.binding import Runtime, make_binding
from trowel_py.agent_host.hub import SessionHub


def cc_payload(workdir: Path, **overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "runtime": "claude_code",
        "workdir": str(workdir),
    }
    payload.update(overrides)
    return payload


def codex_payload(workdir: Path, **overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "runtime": "codex",
        "workdir": str(workdir),
    }
    payload.update(overrides)
    return payload


def create_session(
    client: TestClient,
    payload: dict[str, Any],
) -> dict[str, Any]:
    response = client.post("/api/agent/sessions", json=payload)
    assert response.status_code == 200
    return response.json()["data"]


def parse_sse(body: bytes) -> list[dict[str, Any]]:
    return [
        json.loads(line.removeprefix(b"data: "))
        for line in body.splitlines()
        if line.startswith(b"data: ")
    ]


def put_cc_binding(
    hub: SessionHub,
    workdir: Path,
    *,
    native_session_id: str | None,
) -> str:
    binding = make_binding(
        session_id="history-cc",
        runtime=Runtime.CLAUDE_CODE,
        native_session_id=native_session_id,
        workdir=str(workdir),
        model="glm-5.2",
        effort=None,
        permission="bypassPermissions",
        memory_enabled=True,
        profile_enabled=True,
        capabilities=("tools", "approval", "checkpoint"),
        name="project",
    )
    hub.store.put(binding)
    return binding.session_id
