from __future__ import annotations

import json
from pathlib import Path

import pytest

from trowel_py.memory.mcp_config import write_mcp_config


@pytest.mark.parametrize("memory_enabled", [False, True])
def test_cc_agent_mcp_is_independent_from_memory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    memory_enabled: bool,
) -> None:
    monkeypatch.setenv("TROWEL_MCP_CONFIG_DIR", str(tmp_path))
    path = write_mcp_config(
        trowel_session_id="parent-cc",
        runtime="claude_code",
        workdir=str(tmp_path),
        permission="bypassPermissions",
        memory_enabled=memory_enabled,
        agent_mcp_enabled=True,
        memory_root=str(tmp_path / "memory"),
        base_url="http://127.0.0.1:8123",
    )

    payload = json.loads(path.read_text(encoding="utf-8"))
    servers = payload["mcpServers"]
    assert ("memory" in servers) is memory_enabled
    agent = servers["trowel_agents"]
    assert agent["alwaysLoad"] is True
    assert agent["args"] == ["-m", "trowel_py.agent_mcp.server"]
    assert agent["env"]["TROWEL_PARENT_SESSION_ID"] == "parent-cc"
    assert agent["env"]["TROWEL_PARENT_WORKDIR"] == str(tmp_path)
    assert agent["env"]["TROWEL_PARENT_PERMISSION"] == "bypassPermissions"
    assert agent["env"]["TROWEL_AGENT_BASE_URL"] == "http://127.0.0.1:8123"


def test_cc_delegate_roster_can_keep_memory_without_recursive_agent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TROWEL_MCP_CONFIG_DIR", str(tmp_path))
    path = write_mcp_config(
        trowel_session_id="delegate-cc",
        runtime="claude_code",
        workdir=str(tmp_path),
        permission="bypassPermissions",
        memory_enabled=True,
        agent_mcp_enabled=False,
        memory_root=str(tmp_path / "memory"),
        base_url="http://127.0.0.1:8123",
    )

    servers = json.loads(path.read_text(encoding="utf-8"))["mcpServers"]
    assert set(servers) == {"memory"}
