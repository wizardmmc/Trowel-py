from __future__ import annotations

from trowel_py.agent_mcp import AGENT_MCP_TOOL_NAMES
from trowel_py.agent_mcp.launch import (
    AGENT_MCP_SERVER_NAME,
    build_agent_mcp_launch_spec,
)


def test_launch_spec_owns_shared_agent_mcp_process_facts() -> None:
    spec = build_agent_mcp_launch_spec(
        trowel_session_id="parent",
        runtime="claude_code",
        workdir="/tmp/project",
        permission="bypassPermissions",
        base_url="http://127.0.0.1:8000",
        memory_enabled=True,
        profile_enabled=False,
        self_enabled=True,
        delegation_depth=1,
        native_session_id=None,
        extra_env={"MEMORY_ROOT": "/tmp/memory"},
    )

    assert spec.server_name == AGENT_MCP_SERVER_NAME == "trowel_agents"
    assert spec.module_args == ("-m", "trowel_py.agent_mcp.server")
    assert spec.enabled_tools == AGENT_MCP_TOOL_NAMES
    assert spec.env == {
        "TROWEL_AGENT_BASE_URL": "http://127.0.0.1:8000",
        "TROWEL_PARENT_SESSION_ID": "parent",
        "TROWEL_PARENT_RUNTIME": "claude_code",
        "TROWEL_PARENT_WORKDIR": "/tmp/project",
        "TROWEL_PARENT_PERMISSION": "bypassPermissions",
        "TROWEL_PARENT_MEMORY_ENABLED": "true",
        "TROWEL_PARENT_PROFILE_ENABLED": "false",
        "TROWEL_PARENT_SELF_ENABLED": "true",
        "TROWEL_DELEGATION_DEPTH": "1",
        "MEMORY_ROOT": "/tmp/memory",
    }


def test_launch_spec_distinguishes_unknown_from_empty_native_session_id() -> None:
    common = {
        "trowel_session_id": "parent",
        "runtime": "codex",
        "workdir": "/tmp/project",
        "permission": "danger-full-access",
        "base_url": "http://127.0.0.1:8000",
        "memory_enabled": True,
        "profile_enabled": True,
        "self_enabled": True,
        "delegation_depth": 0,
    }

    fresh_codex = build_agent_mcp_launch_spec(
        **common,
        native_session_id="",
    )
    cc_before_start = build_agent_mcp_launch_spec(
        **common,
        native_session_id=None,
    )

    assert fresh_codex.env["TROWEL_NATIVE_SESSION_ID"] == ""
    assert "TROWEL_NATIVE_SESSION_ID" not in cc_before_start.env


def test_launch_spec_keeps_api_auth_separate_from_process_registration() -> None:
    """API Bearer 与资源登记三元组可以同时存在且不互相冒充。"""

    spec = build_agent_mcp_launch_spec(
        trowel_session_id="parent",
        runtime="codex",
        workdir="/tmp/project",
        permission="danger-full-access",
        base_url="http://127.0.0.1:8000",
        memory_enabled=True,
        profile_enabled=True,
        self_enabled=True,
        delegation_depth=0,
        agent_api_credential="agent-api-secret",
        extra_env={
            "TROWEL_RESOURCE_REGISTRATION_URL": "http://127.0.0.1/register",
            "TROWEL_RESOURCE_REGISTRATION_CREDENTIAL": "registration-secret",
            "TROWEL_RESOURCE_REGISTRATION_TOKEN": "registration-token",
        },
    )

    assert spec.env["TROWEL_AGENT_API_CREDENTIAL"] == "agent-api-secret"
    assert (
        spec.env["TROWEL_RESOURCE_REGISTRATION_CREDENTIAL"]
        == "registration-secret"
    )
