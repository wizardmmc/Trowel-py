"""为 CC 会话写入不依赖 MCP SDK 的 composite stdio MCP 配置。"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path


def _config_path(trowel_session_id: str) -> Path:
    legacy = os.environ.get("TROWEL_MCP_CONFIG")
    if legacy:
        legacy_path = Path(legacy)
        if not trowel_session_id:
            return legacy_path
        return legacy_path.with_name(
            f"{legacy_path.stem}-{trowel_session_id}{legacy_path.suffix or '.json'}"
        )
    directory = Path(
        os.environ.get(
            "TROWEL_MCP_CONFIG_DIR",
            str(Path.home() / ".trowel" / "mcp-configs"),
        )
    )
    name = f"{trowel_session_id}.json" if trowel_session_id else "memory.json"
    return directory / name


def write_mcp_config(
    *,
    trowel_session_id: str = "",
    runtime: str = "claude_code",
    workdir: str = "",
    permission: str = "",
    memory_enabled: bool = True,
    agent_mcp_enabled: bool = False,
    memory_root: str = "",
    base_url: str = "",
    profile_enabled: bool = True,
    self_enabled: bool = True,
    delegation_depth: int = 0,
) -> Path:
    """按会话写入 memory/agent MCP roster，并返回隔离配置路径。"""

    path = _config_path(trowel_session_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    servers: dict[str, object] = {}
    if memory_enabled:
        servers["memory"] = {
            "type": "stdio",
            "command": sys.executable,
            "args": ["-m", "trowel_py.memory.mcp_server"],
        }
    if agent_mcp_enabled:
        servers["trowel_agents"] = {
            "type": "stdio",
            "command": sys.executable,
            "args": ["-m", "trowel_py.agent_mcp.server"],
            "alwaysLoad": True,
            "env": {
                "TROWEL_AGENT_BASE_URL": base_url,
                "TROWEL_PARENT_SESSION_ID": trowel_session_id,
                "TROWEL_PARENT_RUNTIME": runtime,
                "TROWEL_PARENT_WORKDIR": workdir,
                "TROWEL_PARENT_PERMISSION": permission,
                "TROWEL_PARENT_MEMORY_ENABLED": str(memory_enabled).lower(),
                "TROWEL_PARENT_PROFILE_ENABLED": str(profile_enabled).lower(),
                "TROWEL_PARENT_SELF_ENABLED": str(self_enabled).lower(),
                "TROWEL_DELEGATION_DEPTH": str(delegation_depth),
                "MEMORY_ROOT": memory_root,
            },
        }
    config = {"mcpServers": servers}
    path.write_text(json.dumps(config, ensure_ascii=False), encoding="utf-8")
    return path
