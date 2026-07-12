"""tests for write_mcp_config — the SDK-free config writer.

Lives outside test_mcp_server.py on purpose: this imports only ``mcp_config``
(which has no ``mcp`` SDK dependency), proving session creation works even
when the ``mcp`` package is absent. Only the spawned subprocess needs the SDK.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from trowel_py.memory.mcp_config import write_mcp_config


def test_write_mcp_config_shape(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The config JSON has the stdio shape cc requires (C-10: explicit type)."""
    import json

    cfg = tmp_path / "mcp.json"
    monkeypatch.setenv("TROWEL_MCP_CONFIG", str(cfg))
    out = write_mcp_config()
    assert out == cfg
    data = json.loads(cfg.read_text(encoding="utf-8"))
    srv = data["mcpServers"]["memory"]
    assert srv["type"] == "stdio"  # C-10: explicit, else cc cleanup skips signal escalation
    assert srv["command"]
    assert "-m" in srv["args"]
    assert "trowel_py.memory.mcp_server" in srv["args"]
