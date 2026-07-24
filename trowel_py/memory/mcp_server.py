"""memory MCP 的稳定模块入口与 stdio 运行边界。"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any, cast

import mcp.types as types
from mcp.server import NotificationOptions, Server
from mcp.server.stdio import stdio_server

from trowel_py.memory.access_log import log_access, log_outcome, read_access_log
from trowel_py.memory.mcp.handlers import (
    _DICT_L0,
    _REQUIRES_READ_KINDS,
    _TOOL_OUTCOME,
    _TOOL_READ,
    _TOOL_SEARCH,
    _URI_PREFIX,
    _hit as _handler_hit,
    _now,
    _today,
    handle_outcome as _handle_outcome,
    handle_read as _handle_read,
    handle_search as _handle_search,
    parse_memory_uri,
    requires_read,
)
from trowel_py.memory.store import MemoryStore
from trowel_py.memory.types import Note

__all__ = [
    "_DICT_L0",
    "_REQUIRES_READ_KINDS",
    "_SEARCH_DESC",
    "_TOOL_OUTCOME",
    "_TOOL_READ",
    "_TOOL_SEARCH",
    "_URI_PREFIX",
    "_build_server",
    "_hit",
    "_identity_from_env",
    "_now",
    "_today",
    "_tooluse_id",
    "handle_outcome",
    "handle_read",
    "handle_search",
    "main",
    "parse_memory_uri",
    "requires_read",
]

logger = logging.getLogger(__name__)


def _identity_from_env() -> dict[str, str]:
    """读取宿主无关身份，并兼容旧版 CC 环境变量。"""
    cc_session_id = os.environ.get("CC_SESSION_ID", "")
    host_kind = os.environ.get("TROWEL_HOST_KIND", "")
    native_session_id = os.environ.get("TROWEL_NATIVE_SESSION_ID", "")
    if not host_kind and cc_session_id:
        host_kind = "cc"
        native_session_id = native_session_id or cc_session_id
    return {
        "trowel_session_id": os.environ.get("TROWEL_SESSION_ID", ""),
        "cc_session_id": cc_session_id,
        "host_kind": host_kind,
        "native_session_id": native_session_id,
    }


def _tooluse_id(meta: object) -> str:
    if meta is None:
        return ""
    dump = (
        meta.model_dump(exclude_none=True)
        if hasattr(meta, "model_dump")
        else dict(cast(Any, meta))
    )
    return str(dump.get("claudecode/toolUseId", ""))


def _hit(note_id: str, note: Note, rank: int) -> dict[str, Any]:
    return _handler_hit(note_id, note, rank, requires_read_fn=requires_read)


def handle_search(
    query: str,
    top_k: int,
    include_inactive: bool,
    store: MemoryStore,
    dictionary_path: Path,
    identity: dict[str, str],
    toolUseId: str = "",
    retriever: Any = None,
) -> dict[str, Any]:
    return _handle_search(
        query,
        top_k,
        include_inactive,
        store,
        dictionary_path,
        identity,
        toolUseId,
        retriever,
        now_fn=_now,
        hit_fn=_hit,
        log_access_fn=log_access,
    )


def handle_read(
    uri: str,
    search_id: str,
    store: MemoryStore,
    identity: dict[str, str],
    toolUseId: str = "",
) -> dict[str, Any]:
    return _handle_read(
        uri,
        search_id,
        store,
        identity,
        toolUseId,
        parse_uri_fn=parse_memory_uri,
        now_fn=_now,
        today_fn=_today,
        log_access_fn=log_access,
    )


def handle_outcome(
    read_id: str,
    outcome: str,
    reason: str,
    root: Path,
    identity: dict[str, str],
    toolUseId: str = "",
) -> dict[str, Any]:
    return _handle_outcome(
        read_id,
        outcome,
        reason,
        root,
        identity,
        toolUseId,
        now_fn=_now,
        read_access_log_fn=read_access_log,
        log_outcome_fn=log_outcome,
    )


_SEARCH_DESC = (
    "Search memory notes by query. Returns candidates (title+summary+uri). "
    "Hits with requires_read=true must be opened with memory.read — don't "
    "rely on the summary alone."
)


def _build_server(root: Path) -> Server:
    """构造保留请求元数据的三个 memory 工具。"""
    server = Server("memory")
    store = MemoryStore(root)
    dictionary_path = root / _DICT_L0

    @server.list_tools()
    async def list_tools() -> list[types.Tool]:
        return [
            types.Tool(
                name=_TOOL_SEARCH,
                description=_SEARCH_DESC,
                inputSchema={
                    "type": "object",
                    "properties": {
                        "query": {"type": "string"},
                        "top_k": {"type": "integer", "default": 5},
                        "include_inactive": {"type": "boolean", "default": False},
                    },
                    "required": ["query"],
                },
            ),
            types.Tool(
                name=_TOOL_READ,
                description="Read a memory note body by uri (memory://notes/<id>).",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "uri": {"type": "string"},
                        "search_id": {"type": "string"},
                    },
                    "required": ["uri"],
                },
            ),
            types.Tool(
                name=_TOOL_OUTCOME,
                description="Feedback after reading: was the note helpful/harmful/unused?",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "read_id": {"type": "string"},
                        "outcome": {
                            "type": "string",
                            "enum": ["helpful", "harmful", "unused", "unknown"],
                        },
                        "reason": {"type": "string"},
                    },
                    "required": ["read_id", "outcome"],
                },
            ),
        ]

    async def _handle_call_tool(req: types.CallToolRequest) -> types.ServerResult:
        params = req.params
        name = params.name
        args = params.arguments or {}
        identity = _identity_from_env()
        tool_use_id = _tooluse_id(params.meta)
        try:
            if name == _TOOL_SEARCH:
                result = handle_search(
                    query=args.get("query", ""),
                    top_k=int(args.get("top_k", 5)),
                    include_inactive=bool(args.get("include_inactive", False)),
                    store=store,
                    dictionary_path=dictionary_path,
                    identity=identity,
                    toolUseId=tool_use_id,
                )
            elif name == _TOOL_READ:
                result = handle_read(
                    uri=args.get("uri", ""),
                    search_id=args.get("search_id", ""),
                    store=store,
                    identity=identity,
                    toolUseId=tool_use_id,
                )
            elif name == _TOOL_OUTCOME:
                result = handle_outcome(
                    read_id=args.get("read_id", ""),
                    outcome=args.get("outcome", "unknown"),
                    reason=args.get("reason", ""),
                    root=root,
                    identity=identity,
                    toolUseId=tool_use_id,
                )
            else:
                result = {"error": f"unknown tool: {name}"}
        except Exception as exc:
            logger.exception("memory tool %s failed", name)
            result = {"error": f"internal error: {exc!r}"}
        is_error = isinstance(result, dict) and "error" in result
        return types.ServerResult(
            types.CallToolResult(
                content=[
                    types.TextContent(
                        type="text",
                        text=json.dumps(result, ensure_ascii=False),
                    )
                ],
                isError=is_error,
            )
        )

    # 装饰器会丢弃 _meta，必须直接注册底层请求处理器。
    server.request_handlers[types.CallToolRequest] = _handle_call_tool
    return server


async def main() -> None:
    """按 MEMORY_ROOT 启动 stdio MCP server。"""
    root_env = os.environ.get("MEMORY_ROOT", "").strip()
    if root_env:
        root = Path(root_env).expanduser()
    else:
        from trowel_py.memory.paths import resolve_memory_root

        root = resolve_memory_root()
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s"
    )
    server = _build_server(root)
    init_options = server.create_initialization_options(
        notification_options=NotificationOptions(),
        experimental_capabilities={},
    )
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, init_options)


if __name__ == "__main__":
    import asyncio

    asyncio.run(main())
