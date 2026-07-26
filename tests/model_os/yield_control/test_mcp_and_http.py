from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import httpx
import mcp.types as types
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from trowel_py.model_os import mcp_server
from trowel_py.model_os.routes import router
from trowel_py.model_os.yielding import YieldReceipt


def _request(arguments: dict[str, object]) -> types.CallToolRequest:
    return types.CallToolRequest(
        params=types.CallToolRequestParams(name="yield", arguments=arguments)
    )


def _payload(result: types.ServerResult) -> dict[str, object]:
    content = result.root.content
    assert len(content) == 1 and isinstance(content[0], types.TextContent)
    return json.loads(content[0].text)


def test_tool_schema_has_no_authority_or_terminal_fields() -> None:
    tool = mcp_server._tool()
    assert tool.name == "yield"
    assert tool.inputSchema["required"] == [
        "reason",
        "suggested_task_state",
        "current_judgment",
        "next_steps",
        "continue_same_task",
    ]
    for field in (
        "session_id",
        "episode_id",
        "turn_id",
        "generation",
        "terminal",
        "checkpoint",
    ):
        assert field not in tool.inputSchema["properties"]


@pytest.mark.anyio
async def test_mcp_handler_uses_environment_session_and_only_registers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TROWEL_SESSION_ID", "session-1")
    monkeypatch.setenv("TROWEL_MODEL_OS_BASE_URL", "http://trowel.test")
    requests: list[tuple[str, dict[str, object]]] = []

    def transport(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        requests.append((request.url.path, body))
        return httpx.Response(
            200,
            json={
                "success": True,
                "data": {"status": "registered", "episode_id": "episode-1"},
                "error": None,
            },
        )

    monkeypatch.setattr(
        mcp_server,
        "_client",
        lambda: httpx.AsyncClient(
            base_url="http://trowel.test", transport=httpx.MockTransport(transport)
        ),
    )
    handler = mcp_server._build_server().request_handlers[types.CallToolRequest]
    result = await handler(
        _request(
            {
                "reason": "phase complete",
                "suggested_task_state": "ready",
                "current_judgment": "trace verified",
                "next_steps": ["write report"],
                "continue_same_task": True,
            }
        )
    )

    assert _payload(result)["status"] == "registered"
    assert requests == [
        (
            "/api/model-os/sessions/session-1/yield",
            {
                "reason": "phase complete",
                "suggested_task_state": "ready",
                "current_judgment": "trace verified",
                "next_steps": ["write report"],
                "continue_same_task": True,
            },
        )
    ]


def test_http_boundary_requires_managed_binding_and_delegates_proposal() -> None:
    seen: list[tuple[str, object]] = []

    class Coordinator:
        async def propose(self, session_id: str, proposal: object) -> YieldReceipt:
            seen.append((session_id, proposal))
            return YieldReceipt("registered", "episode-1")

    class Hub:
        def get(self, session_id: str):
            assert session_id == "session-1"
            return SimpleNamespace(model_os_mcp_enabled=True)

    app = FastAPI()
    app.state.agent_hub = Hub()
    app.state.model_os_yield_coordinator = Coordinator()
    app.include_router(router, prefix="/api/model-os")
    client = TestClient(app)

    response = client.post(
        "/api/model-os/sessions/session-1/yield",
        json={
            "reason": "phase complete",
            "suggested_task_state": "ready",
            "current_judgment": "trace verified",
            "next_steps": ["write report"],
            "continue_same_task": True,
        },
    )

    assert response.status_code == 200
    assert response.json()["data"] == {
        "status": "registered",
        "episode_id": "episode-1",
        "checkpoint_ref": None,
    }
    assert seen and seen[0][0] == "session-1"


def test_cc_and_codex_rosters_only_add_model_os_when_enabled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from trowel_py.codex_host.session_types import build_default_trowel_model_os_mcp
    from trowel_py.memory.mcp_config import write_mcp_config

    monkeypatch.setenv("TROWEL_MCP_CONFIG_DIR", str(tmp_path))
    path = write_mcp_config(
        trowel_session_id="cc-session",
        memory_enabled=False,
        agent_mcp_enabled=False,
        model_os_mcp_enabled=True,
        base_url="http://127.0.0.1:8123",
    )
    servers = json.loads(path.read_text(encoding="utf-8"))["mcpServers"]
    assert set(servers) == {"trowel_model_os"}
    assert servers["trowel_model_os"]["args"] == [
        "-m",
        "trowel_py.model_os.mcp_server",
    ]

    codex = build_default_trowel_model_os_mcp(
        trowel_session_id="codex-session",
        base_url="http://127.0.0.1:8123",
    ).to_thread_config(native_session_id="thread-1")["trowel_model_os"]
    assert codex["enabled_tools"] == ["yield"]
    assert codex["env"]["TROWEL_NATIVE_SESSION_ID"] == "thread-1"
