"""slice-061 C-8: keep memory tests off the real home.

Several review_job / judge tests reach the real-CCHost branch (``host_factory=
None`` with a monkeypatched ``CCHost``), which calls ``write_mcp_config()`` to
build the ``--mcp-config`` path. Without a redirect that writes
``~/.trowel/memory-mcp-config.json`` — polluting the user's real home (and on
CI, failing on read-only ``$HOME``). Redirect ``TROWEL_MCP_CONFIG`` to a
per-test tmp path, mirroring the cc_host suite's conftest.
"""
from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _redirect_mcp_config(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv(
        "TROWEL_MCP_CONFIG", str(tmp_path / "memory-mcp-config.json")
    )
