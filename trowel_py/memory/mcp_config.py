"""cc MCP-spawn config writer for the memory server (slice-040-c).

This module is intentionally **mcp-SDK-free**: it only writes the JSON config
that tells cc how to spawn ``python -m trowel_py.memory.mcp_server``. It is
kept separate from ``mcp_server.py`` (which imports the heavy ``mcp`` SDK at
module top) so callers that merely need the config path — the FastAPI
``/api/cc/sessions`` route and the review job — do not drag the SDK into the
web-server process. If the SDK is absent, session creation still works; only
the spawned subprocess (which itself requires the SDK) degrades.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path


def write_mcp_config() -> Path:
    """Write the mcp-config JSON pointing at this server, return its path.

    Idempotent (overwrites) so the command path stays current as the venv
    moves. The path is ``~/.trowel/memory-mcp-config.json`` (or
    ``$TROWEL_MCP_CONFIG``). CCHost passes it to cc via
    ``--mcp-config --strict-mcp-config`` so cc spawns this server as a stdio
    subprocess per session.
    """
    path = Path(
        os.environ.get("TROWEL_MCP_CONFIG", str(Path.home() / ".trowel" / "memory-mcp-config.json"))
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    config = {
        "mcpServers": {
            "memory": {
                "type": "stdio",
                "command": sys.executable,
                "args": ["-m", "trowel_py.memory.mcp_server"],
            }
        }
    }
    path.write_text(json.dumps(config, ensure_ascii=False), encoding="utf-8")
    return path
