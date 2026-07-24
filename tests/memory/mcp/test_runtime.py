"""MCP 请求分发边界。"""

from __future__ import annotations

import json
from pathlib import Path

import mcp.types as types
import pytest

from tests.memory.mcp.support import IDENTITY
import trowel_py.memory.mcp_server as mcp_server
from trowel_py.memory.access_log import read_access_log
from trowel_py.memory.store import MemoryStore


def _request(
    name: str,
    arguments: dict[str, object] | None = None,
    *,
    tool_use_id: str = "",
) -> types.CallToolRequest:
    meta = {"claudecode/toolUseId": tool_use_id} if tool_use_id else None
    return types.CallToolRequest(
        params=types.CallToolRequestParams(
            name=name,
            arguments=arguments,
            _meta=meta,
        )
    )


def _payload(result: types.ServerResult) -> dict[str, object]:
    content = result.root.content
    assert len(content) == 1
    assert isinstance(content[0], types.TextContent)
    return json.loads(content[0].text)


def test_facade_patches_reach_read_handler(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = MemoryStore(tmp_path)
    store.write_note(
        {
            "type": "note",
            "title": "N",
            "summary": "s",
            "kind": "fact",
            "verification": "verified",
            "refs": 0,
            "last_ref": "",
        }
    )
    monkeypatch.setattr(mcp_server, "parse_memory_uri", lambda uri: "N")
    monkeypatch.setattr(mcp_server, "_today", lambda: "2030-01-02")
    monkeypatch.setattr(mcp_server, "_now", lambda: "2030-01-02T03:04:05+00:00")

    result = mcp_server.handle_read("patched://uri", "", store, IDENTITY)

    assert "read_id" in result
    assert store.load_note("N").last_ref == "2030-01-02"
    assert read_access_log(tmp_path)[0].ts == "2030-01-02T03:04:05+00:00"


@pytest.mark.asyncio
async def test_dispatch_forwards_meta_tool_use_id(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    received: dict[str, object] = {}

    def fake_read(**kwargs):
        received.update(kwargs)
        return {"ok": True}

    monkeypatch.setattr(mcp_server, "handle_read", fake_read)
    handler = mcp_server._build_server(tmp_path).request_handlers[types.CallToolRequest]

    result = await handler(
        _request(
            "read",
            {"uri": "memory://notes/a", "search_id": "search-1"},
            tool_use_id="tool-1",
        )
    )

    assert received["toolUseId"] == "tool-1"
    assert received["uri"] == "memory://notes/a"
    assert _payload(result) == {"ok": True}
    assert result.root.isError is False


@pytest.mark.asyncio
async def test_dispatch_marks_unknown_tool_as_error(tmp_path: Path) -> None:
    handler = mcp_server._build_server(tmp_path).request_handlers[types.CallToolRequest]

    result = await handler(_request("missing"))

    assert _payload(result) == {"error": "unknown tool: missing"}
    assert result.root.isError is True


@pytest.mark.asyncio
async def test_dispatch_wraps_handler_exception(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail(**kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(mcp_server, "handle_search", fail)
    handler = mcp_server._build_server(tmp_path).request_handlers[types.CallToolRequest]

    result = await handler(_request("search", {"query": "q"}))

    assert _payload(result) == {"error": "internal error: RuntimeError('boom')"}
    assert result.root.isError is True
