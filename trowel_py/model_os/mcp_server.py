"""Model OS 托管会话使用的协作式 yield stdio MCP。"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import Any

import httpx
import mcp.types as types
from mcp.server import NotificationOptions, Server
from mcp.server.stdio import stdio_server

_SERVER_NAME = "trowel_model_os"
_TOOL_NAME = "yield"


def _tool() -> types.Tool:
    return types.Tool(
        name=_TOOL_NAME,
        description=(
            "Propose ending the current Model OS work episode after a natural "
            "phase is complete or a verifiable external wait begins. Do not call "
            "only because the task is difficult, and do not invent completion. "
            "This registers a proposal only; it does not claim that tools, output, "
            "or the turn have finished."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "reason": {"type": "string", "minLength": 1},
                "suggested_task_state": {
                    "type": "string",
                    "enum": ["ready", "waiting_event", "waiting_user", "done"],
                },
                "waiting_condition": {
                    "type": "object",
                    "properties": {
                        "cause": {"type": "string", "minLength": 1},
                        "condition_kind": {"type": "string", "minLength": 1},
                        "target_ref": {"type": "string", "minLength": 1},
                        "match_params": {"type": "object"},
                        "deadline": {"type": "string"},
                    },
                    "required": ["cause", "condition_kind", "target_ref"],
                    "additionalProperties": False,
                },
                "current_judgment": {"type": "string", "minLength": 1},
                "next_steps": {
                    "type": "array",
                    "items": {"type": "string", "minLength": 1},
                    "maxItems": 3,
                },
                "continue_same_task": {"type": "boolean"},
            },
            "required": [
                "reason",
                "suggested_task_state",
                "current_judgment",
                "next_steps",
                "continue_same_task",
            ],
            "additionalProperties": False,
        },
    )


def _session_id() -> str:
    session_id = os.environ.get("TROWEL_SESSION_ID", "").strip()
    if not session_id:
        raise RuntimeError("TROWEL_SESSION_ID is required")
    return session_id


def _base_url() -> str:
    raw = os.environ.get("TROWEL_MODEL_OS_BASE_URL", "").strip()
    url = httpx.URL(raw)
    if url.scheme not in {"http", "https"} or not url.host:
        raise RuntimeError("TROWEL_MODEL_OS_BASE_URL must be an HTTP URL")
    return str(url).rstrip("/")


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(base_url=_base_url(), timeout=30.0)


def _text(payload: dict[str, Any]) -> list[types.TextContent]:
    return [types.TextContent(type="text", text=json.dumps(payload, ensure_ascii=False))]


def _build_server() -> Server:
    server = Server(_SERVER_NAME)

    @server.list_tools()
    async def list_tools() -> list[types.Tool]:
        return [_tool()]

    @server.call_tool()
    async def call_tool(name: str, arguments: dict[str, Any]) -> list[types.TextContent]:
        if name != _TOOL_NAME:
            raise ValueError(f"unknown tool: {name}")
        body = dict(arguments)
        async with _client() as client:
            response = await client.post(
                f"/api/model-os/sessions/{_session_id()}/yield", json=body
            )
            response.raise_for_status()
        payload = response.json()
        data = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(data, dict):
            raise RuntimeError("yield endpoint returned no data")
        return _text(data)

    return server


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    server = _build_server()
    init_options = server.create_initialization_options(
        notification_options=NotificationOptions(), experimental_capabilities={}
    )
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, init_options)


if __name__ == "__main__":
    asyncio.run(main())
