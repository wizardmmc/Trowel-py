"""为 CC 会话写入不依赖 MCP SDK 的 stdio server 配置。"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path


def write_mcp_config() -> Path:
    """覆盖写入当前解释器对应的 MCP 配置，并返回配置路径。"""
    path = Path(
        os.environ.get(
            "TROWEL_MCP_CONFIG",
            str(Path.home() / ".trowel" / "memory-mcp-config.json"),
        )
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
