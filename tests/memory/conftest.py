"""把 memory MCP 配置隔离到临时目录，禁止测试污染真实用户目录。"""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _redirect_mcp_config(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("TROWEL_MCP_CONFIG", str(tmp_path / "memory-mcp-config.json"))
