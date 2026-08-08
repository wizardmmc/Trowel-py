from __future__ import annotations

from pathlib import Path

import pytest

from tests.cc_host.service._support import (
    FakeProc,
    FakeSpawner,
    collect,
    init_event,
    line,
    result_ok,
)
from trowel_py.cc_host.service import CCHost


@pytest.fixture(autouse=True)
def _isolated_memory_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "trowel_py.memory.paths.resolve_memory_root",
        lambda config_path=None: tmp_path,
    )
    monkeypatch.setattr(
        "trowel_py.memory.injection.resolve_memory_root",
        lambda config_path=None: tmp_path,
    )


async def test_spawn_args_include_mcp_config(tmp_path: Path) -> None:
    process = FakeProc([line(init_event()), line(result_ok())])
    spawner = FakeSpawner([process])
    config_path = tmp_path / "memory-mcp.json"
    host = CCHost(
        "session-id",
        tmp_path,
        spawner=spawner,
        mcp_config=str(config_path),
    )

    await collect(host.send("hi"))

    args = spawner.spawned[0][0]
    index = args.index("--mcp-config")
    assert args[index : index + 3] == [
        "--mcp-config",
        str(config_path),
        "--strict-mcp-config",
    ]


async def test_private_settings_do_not_disable_connection_user_config(
    tmp_path: Path,
) -> None:
    """会话私有 settings 只做 provider 覆盖，不能再清空用户配置源。"""

    process = FakeProc([line(init_event()), line(result_ok())])
    spawner = FakeSpawner([process])
    settings_path = tmp_path / "provider-settings.json"
    settings_path.write_text("{}", encoding="utf-8")
    host = CCHost(
        "session-id",
        tmp_path,
        spawner=spawner,
        settings_path=settings_path,
    )

    await collect(host.send("hi"))

    args = spawner.spawned[0][0]
    assert args[args.index("--settings") + 1] == str(settings_path)
    assert "--setting-sources" not in args


def test_build_spawn_env_points_to_frozen_connection_home(tmp_path: Path) -> None:
    """Claude 用户状态按连接隔离，插件仍可共享同一份物理安装。"""

    config_home = tmp_path / "connection-home"
    plugin_home = tmp_path / "global-plugins"
    host = CCHost(
        "session-id",
        tmp_path,
        proxy_base_url=None,
        claude_config_dir=config_home,
        claude_plugin_dir=plugin_home,
    )

    env = host._build_spawn_env()

    assert env is not None
    assert env["CLAUDE_CONFIG_DIR"] == str(config_home)
    assert env["CLAUDE_CODE_PLUGIN_CACHE_DIR"] == str(plugin_home)
    assert host.projects_root == config_home / "projects"


async def test_agent_mcp_tools_are_preapproved_for_claude(tmp_path: Path) -> None:
    process = FakeProc([line(init_event()), line(result_ok())])
    spawner = FakeSpawner([process])
    host = CCHost(
        "session-id",
        tmp_path,
        spawner=spawner,
        mcp_config=str(tmp_path / "agent-mcp.json"),
        agent_mcp_enabled=True,
    )

    await collect(host.send("hi"))

    args = spawner.spawned[0][0]
    index = args.index("--allowedTools")
    assert args[index + 1].split(",") == [
        "mcp__trowel_agents__delegate",
        "mcp__trowel_agents__delegate_start",
        "mcp__trowel_agents__delegate_respond",
        "mcp__trowel_agents__delegate_status",
        "mcp__trowel_agents__delegate_close",
    ]


def test_build_spawn_env_injects_identity_when_mcp_config(tmp_path: Path) -> None:
    host = CCHost(
        "session-id",
        tmp_path,
        mcp_config=str(tmp_path / "memory-mcp.json"),
        proxy_base_url=None,
    )

    env = host._build_spawn_env()

    assert env is not None
    assert env["TROWEL_SESSION_ID"] == "session-id"
    assert env["TROWEL_HOST_KIND"] == "cc"
    assert env["MEMORY_ROOT"] == str(tmp_path)
    assert "TROWEL_NATIVE_SESSION_ID" not in env
    assert "CC_SESSION_ID" not in env


def test_build_spawn_env_native_session_id_when_resumed(tmp_path: Path) -> None:
    host = CCHost(
        "session-id",
        tmp_path,
        mcp_config=str(tmp_path / "memory-mcp.json"),
        proxy_base_url=None,
        resume_from="native-session-id",
    )

    env = host._build_spawn_env()

    assert env is not None
    assert env["TROWEL_SESSION_ID"] == "session-id"
    assert env["TROWEL_HOST_KIND"] == "cc"
    assert env["TROWEL_NATIVE_SESSION_ID"] == "native-session-id"
    assert env["CC_SESSION_ID"] == "native-session-id"
    assert env["MEMORY_ROOT"] == str(tmp_path)


def test_build_spawn_env_no_identity_without_mcp_config(tmp_path: Path) -> None:
    host = CCHost("session-id", tmp_path, proxy_base_url=None)

    assert host._build_spawn_env() is None


def test_discussion_spawn_env_removes_trowel_private_root_hints(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """participant 子进程不继承可直接定位 discussion/Memory 私有数据的变量。"""

    for name in (
        "TROWEL_DATA_ROOT",
        "TROWEL_DESKTOP_DATA_DIR",
        "TROWEL_AGENT_SESSIONS_PATH",
        "MEMORY_ROOT",
        "TROWEL_MEMORY_ROOT",
    ):
        monkeypatch.setenv(name, f"/private/{name}")
    host = CCHost(
        "discussion-session",
        tmp_path,
        proxy_base_url=None,
        session_kind="discussion",
        memory_enabled=False,
        profile_enabled=False,
        self_enabled=False,
        agent_mcp_enabled=False,
    )

    env = host._build_spawn_env()

    assert env is not None
    assert all(
        name not in env
        for name in (
            "TROWEL_DATA_ROOT",
            "TROWEL_DESKTOP_DATA_DIR",
            "TROWEL_AGENT_SESSIONS_PATH",
            "MEMORY_ROOT",
            "TROWEL_MEMORY_ROOT",
        )
    )


def test_agent_mcp_startup_timeouts_are_bounded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MCP_CONNECTION_NONBLOCKING", "true")
    monkeypatch.setenv("MCP_CONNECT_TIMEOUT_MS", "999999")
    monkeypatch.setenv("MCP_TIMEOUT", "999999")
    host = CCHost(
        "session-id",
        tmp_path,
        mcp_config=str(tmp_path / "agent-mcp.json"),
        agent_mcp_enabled=True,
    )

    env = host._build_spawn_env()

    assert env is not None
    assert env["MCP_CONNECTION_NONBLOCKING"] == "false"
    assert env["MCP_CONNECT_TIMEOUT_MS"] == "5000"
    assert env["MCP_TIMEOUT"] == "10000"
