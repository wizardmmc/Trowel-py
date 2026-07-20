"""slice-078: trowel memory MCP wiring on a Codex thread.

Covers the config object that rides on ``thread/start.config.mcp_servers`` and
the manager's translation of ``CodexSessionConfig.trowel_memory_mcp`` /
``developer_instructions`` into ``thread/start`` params. The four M/P combos'
*content* (what build_memory_injection returns) is exercised at the hub layer;
these tests pin the wire shape only.
"""
from __future__ import annotations

import sys

from trowel_py.codex_host.manager import CodexHostManager
from trowel_py.codex_host.protocol import TROWEL_NOTE_SEARCH_SERVER_NAME
from trowel_py.codex_host.session import (
    CodexSession,
    CodexSessionConfig,
    TrowelMemoryMcpConfig,
    build_default_trowel_memory_mcp,
)


def test_to_thread_config_shape_carries_identity_env() -> None:
    """The MCP server dict carries the host-neutral identity env + tools.

    On a fresh thread/start the native thread_id is unknown, so
    TROWEL_NATIVE_SESSION_ID is left empty (codex review HIGH-3: do not fake
    it with the trowel session id)."""

    cfg = TrowelMemoryMcpConfig(
        server_name=TROWEL_NOTE_SEARCH_SERVER_NAME,
        command="/usr/bin/python",
        module_args=("-m", "trowel_py.memory.mcp_server"),
        memory_root="/tmp/mem",
        trowel_session_id="trowel-1",
    )
    servers = cfg.to_thread_config()
    name, server = next(iter(servers.items()))
    assert name == TROWEL_NOTE_SEARCH_SERVER_NAME
    assert server["command"] == "/usr/bin/python"
    assert server["args"] == ["-m", "trowel_py.memory.mcp_server"]
    env = server["env"]
    assert env["MEMORY_ROOT"] == "/tmp/mem"
    assert env["TROWEL_SESSION_ID"] == "trowel-1"
    assert env["TROWEL_HOST_KIND"] == "codex"
    # Fresh start: thread_id unknown → leave empty (do NOT fake with trowel id).
    assert env["TROWEL_NATIVE_SESSION_ID"] == ""
    assert server["required"] is True
    assert server["enabled_tools"] == ["search", "read", "outcome"]
    assert server["default_tools_approval_mode"] == "approve"


def test_to_thread_config_resume_stamps_real_thread_id() -> None:
    """On thread/resume the binding already knows the thread_id — stamp it
    for real so the MCP writes honest identity (codex review HIGH-3)."""

    cfg = TrowelMemoryMcpConfig(
        server_name=TROWEL_NOTE_SEARCH_SERVER_NAME,
        command="/usr/bin/python",
        module_args=("-m", "trowel_py.memory.mcp_server"),
        memory_root="/tmp/mem",
        trowel_session_id="trowel-1",
    )
    env = cfg.to_thread_config(native_session_id="codex-thread-abc")[
        TROWEL_NOTE_SEARCH_SERVER_NAME
    ]["env"]
    assert env["TROWEL_NATIVE_SESSION_ID"] == "codex-thread-abc"
    assert env["TROWEL_HOST_KIND"] == "codex"


def test_build_default_trowel_memory_mcp_uses_current_interpreter() -> None:
    """The default factory points at the running interpreter so the spawn works
    regardless of which venv trowel was launched from."""

    cfg = build_default_trowel_memory_mcp(
        trowel_session_id="sid", memory_root="/tmp/mem"
    )
    assert cfg.command == sys.executable
    assert cfg.module_args == ("-m", "trowel_py.memory.mcp_server")
    assert cfg.server_name == TROWEL_NOTE_SEARCH_SERVER_NAME
    assert cfg.memory_root == "/tmp/mem"
    assert cfg.trowel_session_id == "sid"


def test_thread_start_params_includes_mcp_servers_when_memory_on() -> None:
    """memory-on: trowel_memory_mcp set → config.mcp_servers is on the request."""

    manager = CodexHostManager()
    cfg = build_default_trowel_memory_mcp(
        trowel_session_id="sid", memory_root="/tmp/mem"
    )
    session = CodexSession(
        CodexSessionConfig(
            trowel_session_id="sid",
            workdir="/tmp/x",
            trowel_memory_mcp=cfg,
        )
    )
    params = manager._thread_start_params(session)  # noqa: SLF001
    assert params["config"]["mcp_servers"][TROWEL_NOTE_SEARCH_SERVER_NAME][
        "env"
    ]["TROWEL_HOST_KIND"] == "codex"


def test_thread_start_params_omits_config_when_memory_off() -> None:
    """memory-off: trowel_memory_mcp is None → no config key at all (C-3)."""

    manager = CodexHostManager()
    session = CodexSession(
        CodexSessionConfig(
            trowel_session_id="sid",
            workdir="/tmp/x",
            trowel_memory_mcp=None,
        )
    )
    params = manager._thread_start_params(session)  # noqa: SLF001
    assert "config" not in params


def test_thread_start_params_carries_developer_instructions_when_set() -> None:
    """Static injection text flows through to developerInstructions."""

    manager = CodexHostManager()
    session = CodexSession(
        CodexSessionConfig(
            trowel_session_id="sid",
            workdir="/tmp/x",
            developer_instructions="# 用户画像\n...",
        )
    )
    params = manager._thread_start_params(session)  # noqa: SLF001
    assert params["developerInstructions"] == "# 用户画像\n..."


def test_thread_start_params_omits_developer_instructions_when_empty() -> None:
    """An empty injection (memory-off + no profile) maps to None → the key is
    omitted entirely so the app-server does not see a bare empty override."""

    manager = CodexHostManager()
    session = CodexSession(
        CodexSessionConfig(
            trowel_session_id="sid",
            workdir="/tmp/x",
            developer_instructions=None,
        )
    )
    params = manager._thread_start_params(session)  # noqa: SLF001
    assert "developerInstructions" not in params


def test_thread_resume_params_re_attaches_memory_mcp_with_thread_id() -> None:
    """slice-078 HIGH-1 (codex review): resume re-attaches the memory MCP —
    app-server persists model/effort but NOT the MCP config, so a memory-on
    thread resumed after restart would silently lose the read-path. HIGH-3:
    the real thread_id is known at resume, so it is stamped (unlike the
    fresh start path which leaves native_session_id empty)."""

    manager = CodexHostManager()
    cfg = build_default_trowel_memory_mcp(
        trowel_session_id="sid", memory_root="/tmp/mem"
    )
    session = CodexSession(
        CodexSessionConfig(
            trowel_session_id="sid",
            workdir="/tmp/x",
            trowel_memory_mcp=cfg,
            initial_thread_id="thr-existing",
        )
    )
    params = manager._thread_resume_params(session)  # noqa: SLF001
    assert params["threadId"] == "thr-existing"
    env = params["config"]["mcp_servers"][TROWEL_NOTE_SEARCH_SERVER_NAME]["env"]
    assert env["TROWEL_NATIVE_SESSION_ID"] == "thr-existing"
    assert env["TROWEL_HOST_KIND"] == "codex"


def test_thread_resume_params_omits_config_when_memory_off() -> None:
    """A memory-off resumed thread does NOT re-attach any MCP (C-3)."""

    manager = CodexHostManager()
    session = CodexSession(
        CodexSessionConfig(
            trowel_session_id="sid",
            workdir="/tmp/x",
            trowel_memory_mcp=None,
            initial_thread_id="thr-existing",
        )
    )
    params = manager._thread_resume_params(session)  # noqa: SLF001
    assert params == {"threadId": "thr-existing"}
    assert "config" not in params
