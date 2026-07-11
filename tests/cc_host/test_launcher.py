"""unit tests for cc_host.launcher.build_args (slice-040-c).

Pinned startup args + the slice-040-c additions: ``--mcp-config`` (memory MCP
server) and ``--strict-mcp-config``. The MCP config path is a single JSON file
written by the launcher; ``--strict`` makes cc ignore all other MCP sources
(.mcp.json / user settings / plugins) so only the memory server is attached.
"""
from __future__ import annotations

from trowel_py.cc_host.launcher import build_args


def test_build_args_mcp_config_added() -> None:
    """mcp_config path adds --mcp-config <path> --strict-mcp-config."""
    args = build_args("/wd", mcp_config="/tmp/mem.json")
    assert "--mcp-config" in args
    idx = args.index("--mcp-config")
    assert args[idx + 1] == "/tmp/mem.json"
    assert "--strict-mcp-config" in args


def test_build_args_no_mcp_config_by_default() -> None:
    """No mcp_config → neither flag appears (cc attaches no memory server)."""
    args = build_args("/wd")
    assert "--mcp-config" not in args
    assert "--strict-mcp-config" not in args


def test_build_args_mcp_config_with_append_system_prompt() -> None:
    """mcp_config and append_system_prompt coexist (039 injection + 040-c MCP)."""
    args = build_args(
        "/wd",
        mcp_config="/tmp/mem.json",
        append_system_prompt="# memory root\n...",
    )
    assert "--mcp-config" in args
    assert "--strict-mcp-config" in args
    assert "--append-system-prompt" in args
