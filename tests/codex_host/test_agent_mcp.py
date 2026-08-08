from __future__ import annotations

from trowel_py.codex_host.manager import CodexHostManager
from trowel_py.codex_host.session import CodexSession, CodexSessionConfig
from trowel_py.codex_host.session_types import build_default_trowel_agent_mcp


def test_codex_agent_mcp_is_required_and_bounded() -> None:
    cfg = build_default_trowel_agent_mcp(
        trowel_session_id="parent-codex",
        workdir="/tmp/project",
        permission="danger-full-access",
        base_url="http://127.0.0.1:8123",
        memory_enabled=False,
        profile_enabled=False,
        self_enabled=True,
    )
    session = CodexSession(
        CodexSessionConfig(
            trowel_session_id="parent-codex",
            workdir="/tmp/project",
            trowel_agent_mcp=cfg,
        )
    )

    params = CodexHostManager()._thread_start_params(session)  # noqa: SLF001
    server = params["config"]["mcp_servers"]["trowel_agents"]
    assert server["required"] is True
    assert server["startup_timeout_sec"] == 10.0
    assert "tool_timeout_sec" not in server
    assert server["command"]
    assert server["args"] == ["-m", "trowel_py.agent_mcp.server"]
    assert server["enabled_tools"] == [
        "delegate",
        "delegate_start",
        "delegate_respond",
        "delegate_status",
        "delegate_close",
    ]
    assert server["env"]["TROWEL_PARENT_SESSION_ID"] == "parent-codex"
    assert server["env"]["TROWEL_PARENT_PERMISSION"] == "danger-full-access"


def test_codex_agent_mcp_reattaches_on_resume() -> None:
    cfg = build_default_trowel_agent_mcp(
        trowel_session_id="parent-codex",
        workdir="/tmp/project",
        permission="danger-full-access",
        base_url="http://127.0.0.1:8123",
        memory_enabled=True,
        profile_enabled=True,
        self_enabled=True,
    )
    session = CodexSession(
        CodexSessionConfig(
            trowel_session_id="parent-codex",
            workdir="/tmp/project",
            initial_thread_id="thread-existing",
            trowel_agent_mcp=cfg,
        )
    )

    params = CodexHostManager()._thread_resume_params(session)  # noqa: SLF001
    env = params["config"]["mcp_servers"]["trowel_agents"]["env"]
    assert env["TROWEL_NATIVE_SESSION_ID"] == "thread-existing"


def test_codex_agent_mcp_carries_process_registration_env() -> None:
    """Agent MCP 与 Memory MCP 使用各自的 owner 登记令牌。"""

    cfg = build_default_trowel_agent_mcp(
        trowel_session_id="parent-codex",
        workdir="/tmp/project",
        permission="danger-full-access",
        base_url="http://127.0.0.1:8123",
        memory_enabled=False,
        profile_enabled=False,
        self_enabled=True,
        registration_env={"TROWEL_RESOURCE_REGISTRATION_TOKEN": "private-token"},
        agent_api_credential="agent-api-secret",
    )

    env = cfg.to_thread_config()["trowel_agents"]["env"]

    assert env["TROWEL_RESOURCE_REGISTRATION_TOKEN"] == "private-token"
    assert env["TROWEL_AGENT_API_CREDENTIAL"] == "agent-api-secret"
    assert env["TROWEL_PARENT_SESSION_ID"] == "parent-codex"
