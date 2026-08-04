from __future__ import annotations

import json
import os
from pathlib import Path
import stat
import subprocess
import sys

import pytest

from trowel_py.memory import mcp_config


def test_write_mcp_config_shape(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
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


@pytest.mark.skipif(os.name == "nt", reason="Windows 不提供 POSIX 文件权限位")
def test_mcp_config_with_credential_is_private(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = tmp_path / "mcp.json"
    monkeypatch.setenv("TROWEL_MCP_CONFIG", str(cfg))

    mcp_config.write_mcp_config(
        agent_mcp_enabled=True,
        agent_api_credential="desktop-instance-secret",
    )

    assert stat.S_IMODE(cfg.stat().st_mode) == 0o600
    data = json.loads(cfg.read_text(encoding="utf-8"))
    assert data["mcpServers"]["trowel_agents"]["env"][
        "TROWEL_RESOURCE_REGISTRATION_CREDENTIAL"
    ] == "desktop-instance-secret"


@pytest.mark.skipif(os.name == "nt", reason="Windows 不提供 POSIX 文件权限位")
def test_mcp_config_replaces_an_existing_wide_mode_file_privately(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = tmp_path / "mcp.json"
    cfg.write_text("{}", encoding="utf-8")
    cfg.chmod(0o644)
    monkeypatch.setenv("TROWEL_MCP_CONFIG", str(cfg))

    mcp_config.write_mcp_config()

    assert stat.S_IMODE(cfg.stat().st_mode) == 0o600


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
