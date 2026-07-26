from __future__ import annotations

import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from mcp.client.session import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client


class _YieldHandler(BaseHTTPRequestHandler):
    calls: list[tuple[str, dict[str, object]]] = []

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        length = int(self.headers.get("content-length", "0"))
        body = json.loads(self.rfile.read(length))
        self.calls.append((self.path, body))
        payload = json.dumps(
            {
                "success": True,
                "data": {"status": "registered", "episode_id": "episode-1"},
                "error": None,
            }
        ).encode()
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, _format: str, *args: object) -> None:
        return None


@pytest.mark.anyio
@pytest.mark.parametrize("runtime", ["claude_code", "codex"])
async def test_real_stdio_mcp_call_closes_cleanly(runtime: str) -> None:
    _YieldHandler.calls = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), _YieldHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        params = StdioServerParameters(
            command=sys.executable,
            args=["-m", "trowel_py.model_os.mcp_server"],
            env={
                **os.environ,
                "TROWEL_MODEL_OS_BASE_URL": (
                    f"http://127.0.0.1:{server.server_address[1]}"
                ),
                "TROWEL_SESSION_ID": "session-1",
                "TROWEL_PARENT_RUNTIME": runtime,
            },
        )
        async with stdio_client(params) as (read_stream, write_stream):
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()
                tools = await session.list_tools()
                assert [tool.name for tool in tools.tools] == ["yield"]
                result = await session.call_tool(
                    "yield",
                    {
                        "reason": "phase complete",
                        "suggested_task_state": "ready",
                        "current_judgment": "verified",
                        "next_steps": [],
                        "continue_same_task": True,
                    },
                )
                assert result.isError is not True
        assert _YieldHandler.calls == [
            (
                "/api/model-os/sessions/session-1/yield",
                {
                    "reason": "phase complete",
                    "suggested_task_state": "ready",
                    "current_judgment": "verified",
                    "next_steps": [],
                    "continue_same_task": True,
                },
            )
        ]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
