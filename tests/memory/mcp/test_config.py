from __future__ import annotations

from pathlib import Path
import subprocess
import sys

import pytest

from trowel_py.memory import mcp_config


def test_write_mcp_config_shape(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import json

    cfg = tmp_path / "mcp.json"
    monkeypatch.setenv("TROWEL_MCP_CONFIG", str(cfg))
    out = mcp_config.write_mcp_config()
    assert out == cfg
    data = json.loads(cfg.read_text(encoding="utf-8"))
    srv = data["mcpServers"]["memory"]
    # 显式 stdio 类型让 CC 清理子进程时能够升级终止信号。
    assert srv["type"] == "stdio"
    assert srv["command"]
    assert "-m" in srv["args"]
    assert "trowel_py.memory.mcp_server" in srv["args"]


def test_module_import_does_not_require_mcp_sdk() -> None:
    script = """
import importlib.abc
import sys

for name in tuple(sys.modules):
    if name == "mcp" or name.startswith("mcp."):
        del sys.modules[name]

class RejectMcp(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == "mcp" or fullname.startswith("mcp."):
            raise ModuleNotFoundError(f"blocked MCP SDK import: {fullname}")
        return None

sys.meta_path.insert(0, RejectMcp())
from trowel_py.memory import mcp_config
assert callable(mcp_config.write_mcp_config)
"""
    subprocess.run(
        [sys.executable, "-c", script],
        cwd=Path(__file__).parents[3],
        check=True,
        capture_output=True,
        text=True,
    )
