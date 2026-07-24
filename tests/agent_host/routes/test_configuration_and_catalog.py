from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tests.agent_host.routes.support import (
    cc_payload,
    codex_payload,
    create_session,
)
from trowel_py.agent_host.binding import Runtime, make_binding
from trowel_py.agent_host.hub import SessionHub


def test_patch_runtime_change_rejected_422(
    client: TestClient,
    workdir: Path,
) -> None:
    created = create_session(client, cc_payload(workdir))
    response = client.patch(
        f"/api/agent/sessions/{created['session_id']}",
        json={"runtime": "codex"},
    )

    assert response.status_code == 422


def test_patch_codex_model_effort_returns_adjusted_pair(
    client: TestClient,
    workdir: Path,
) -> None:
    created = create_session(
        client,
        codex_payload(workdir, model="gpt-5.6-sol", effort="ultra"),
    )
    response = client.patch(
        f"/api/agent/sessions/{created['session_id']}",
        json={"model": "gpt-5.6-luna", "effort": "ultra"},
    )

    assert response.status_code == 200
    assert response.json()["data"] == {
        "model": "gpt-5.6-luna",
        "effort": "medium",
        "adjusted": True,
    }


def test_cross_resume_rejected_409(
    client: TestClient,
    workdir: Path,
    hub: SessionHub,
) -> None:
    hub.store.put(
        make_binding(
            session_id="old-cc",
            runtime=Runtime.CLAUDE_CODE,
            native_session_id="cc-native-1",
            workdir=str(workdir),
            model=None,
            effort=None,
            permission=None,
            memory_enabled=True,
            profile_enabled=True,
            capabilities=("tools",),
            name="project",
        )
    )

    response = client.post(
        "/api/agent/sessions",
        json=codex_payload(workdir, resume_from="cc-native-1"),
    )

    assert response.status_code == 409


def test_get_runtimes(client: TestClient) -> None:
    response = client.get("/api/agent/runtimes")

    assert response.status_code == 200
    runtimes = response.json()["data"]
    assert {runtime["runtime"] for runtime in runtimes} == {
        "claude_code",
        "codex",
    }
    assert all("capabilities" in runtime for runtime in runtimes)
    codex = next(runtime for runtime in runtimes if runtime["runtime"] == "codex")
    assert codex["connected"] is True


def test_get_models_returns_the_manager_catalog(
    client: TestClient,
    hub: SessionHub,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # API 原样返回原生模型目录，不维护第二份模型白名单。
    native = [
        {
            "id": "future-model",
            "model": "future-model-native",
            "display_name": "Future",
            "description": "Recorded by the fake app-server.",
            "is_default": True,
            "default_effort": "quantum",
            "supported_efforts": [
                {
                    "value": "quantum",
                    "description": "Unknown future effort",
                }
            ],
        }
    ]

    async def list_models() -> list[dict[str, object]]:
        return native

    monkeypatch.setattr(hub._codex, "list_models", list_models)  # noqa: SLF001
    response = client.get("/api/agent/models")

    assert response.status_code == 200
    assert response.json()["data"]["models"] == native


def test_get_history_returns_native_codex_threads_for_workdir(
    client: TestClient,
    hub: SessionHub,
    workdir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "trowel_py.agent_host.history.scan_cc_history",
        lambda _workdir, *, limit: [],
    )
    hub._codex.threads = [  # type: ignore[union-attr]  # noqa: SLF001
        {
            "id": "thread-native-1",
            "preview": "native title",
            "updatedAt": 123,
        }
    ]

    response = client.get(f"/api/agent/sessions?workdir={workdir}")
    assert response.status_code == 200
    rows = response.json()["data"]
    codex_rows = [row for row in rows if row["runtime"] == "codex"]
    assert codex_rows
    assert codex_rows[0]["native_session_id"] == "thread-native-1"
    assert response.json()["meta"] == {"limit": 20, "next_cursor": None}
