"""为 CC 会话写入不依赖 MCP SDK 的 composite stdio MCP 配置。"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path


def _config_path(trowel_session_id: str) -> Path:
    """按环境变量优先级确定当前会话的 MCP 配置路径。

    ``TROWEL_MCP_CONFIG`` 非空时优先使用：会话 ID 为空则原样返回该路径；
    否则在同目录的文件 stem 后追加 ``-<session_id>``，保留原后缀，无后缀时
    补 ``.json``。未设置该变量时，文件写入 ``TROWEL_MCP_CONFIG_DIR``；目录
    变量也未设置时使用 ``~/.trowel/mcp-configs``。文件名是
    ``<session_id>.json``，空会话 ID 则为 ``memory.json``。目录变量被显式
    设为空字符串时，``Path("")`` 指向当前目录，不会使用默认目录。

    会话 ID 不做文件名清洗；调用方必须提供不含路径分隔符或 ``..`` 的安全文件
    名片段，并自行避免不同会话写入同一路径。

    Args:
        trowel_session_id: 用于区分配置文件的 Trowel 会话 ID；空字符串表示使用
            共享的无会话路径。

    Returns:
        根据环境和会话 ID 计算出的目标配置路径。
    """
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
