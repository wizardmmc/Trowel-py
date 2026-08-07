"""提供 Memory MCP 的兼容导入入口和 stdio 服务边界。

模块重导出处理器常量与辅助函数，并通过薄包装器把本模块当前绑定的时间、日志和
URI 依赖传给 ``mcp.handlers``，使调用方对兼容入口的 monkeypatch 继续生效。
``_build_server`` 声明并分发 search、read、outcome 三个工具，``main`` 则选择
Memory 根目录并在标准输入输出上运行服务。
"""

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
from trowel_py.memory.mcp.retriever_factory import (
    LazyRetriever,
    MemoryRetrieverFactory,
    create_default_memory_retriever,
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
    """从进程环境读取当前 Agent 会话身份。

    优先原样使用 ``TROWEL_HOST_KIND`` 和 ``TROWEL_NATIVE_SESSION_ID``。仅当
    ``TROWEL_HOST_KIND == ""`` 且旧版 ``CC_SESSION_ID`` 是非空字符串时，才
    把运行端补成 ``cc``；此时也仅在 ``TROWEL_NATIVE_SESSION_ID == ""`` 时
    用该 CC ID 补齐。函数不去除变量两端空白，也不校验不同身份字段是否彼此
    一致。除上述回退外，环境变量缺失时对应字段为空。

    Returns:
        含 Trowel 会话 ID、旧版 CC 会话 ID、运行端和原生会话 ID 的日志身份。
    """
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
    """从 MCP 请求元数据中提取 Claude Code 工具调用 ID。

    Pydantic 风格对象通过 ``model_dump(exclude_none=True)`` 转成字典，其他非
    ``None`` 对象通过 ``dict(meta)`` 转换。键 ``claudecode/toolUseId`` 缺失
    时返回空字符串；找到的值统一经 ``str`` 转换，因此原始映射中的 ``None``
    会变成 ``"None"``。元数据无法转换时异常直接传播。

    Args:
        meta: MCP 请求的 ``_meta`` 对象或可转成字典的映射；None 表示无元数据。

    Returns:
        宿主工具调用 ID，未提供时为空字符串。
    """
    if meta is None:
        return ""
    dump = (
        meta.model_dump(exclude_none=True)
        if hasattr(meta, "model_dump")
        else dict(cast(Any, meta))
    )
    return str(dump.get("claudecode/toolUseId", ""))


def _hit(note_id: str, note: Note, rank: int) -> dict[str, Any]:
    """用兼容入口当前绑定的 ``requires_read`` 规则构造搜索候选。

    Args:
        note_id: Note 文件 stem。
        note: 要转换的 Note。
        rank: 检索器给出的零起始候选位置。

    Returns:
        ``mcp.handlers._hit`` 构造的搜索结果字典。
    """
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
    """使用本模块当前绑定的依赖执行搜索处理器。

    参数、返回值和副作用遵循 ``mcp.handlers.handle_search``；这里仅把当前
    ``_now``、``_hit`` 和 ``log_access`` 显式传入，保留调用方替换兼容入口
    依赖的能力。

    Args:
        query: 搜索文本。
        top_k: 过滤前截取的候选数量。
        include_inactive: 是否允许返回非活动 Note。
        store: Memory 存储。
        dictionary_path: L0 Dictionary 路径。
        identity: 写入访问日志的会话身份。
        toolUseId: 宿主工具调用 ID。
        retriever: 可选的检索器；None 表示由处理器构造默认检索器。

    Returns:
        搜索结果或领域错误字典。
    """
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
    """使用本模块当前绑定的依赖执行正文读取处理器。

    函数把当前 ``parse_memory_uri``、``_now``、``_today`` 和 ``log_access``
    显式传入底层处理器，保留调用方替换兼容入口依赖的能力。

    Args:
        uri: Memory Note URI。
        search_id: 来源搜索 ID；可为空。
        store: Memory 存储。
        identity: 写入访问日志的会话身份。
        toolUseId: 宿主工具调用 ID。

    Returns:
        Note 正文结果或领域错误字典。
    """
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
    """使用本模块当前绑定的依赖执行读取反馈处理器。

    函数把当前 ``_now``、``read_access_log`` 和 ``log_outcome`` 显式传入底层
    处理器，保留调用方替换兼容入口依赖的能力。

    Args:
        read_id: 被评价的正文读取 ID。
        outcome: ``helpful``、``harmful``、``unused`` 或 ``unknown``。
        reason: 反馈理由。
        root: 访问与反馈日志所在的 Memory 根目录。
        identity: 写入反馈日志的当前会话身份。
        toolUseId: 宿主工具调用 ID。

    Returns:
        写入确认或领域错误字典。
    """
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


def _build_server(
    root: Path,
    *,
    retriever_factory: MemoryRetrieverFactory = create_default_memory_retriever,
) -> Server:
    """构造保留请求元数据的 Memory MCP server。

    Server 复用一个绑定 ``root`` 的 ``MemoryStore``，并公开 search、read、
    outcome 三个工具。为取得完整请求 ``_meta``，调用处理器直接注册到
    ``request_handlers``，没有经过 SDK ``call_tool`` 装饰器；因此工具的
    ``inputSchema`` 用于客户端发现，但服务端不会执行 SDK JSON Schema 校验。

    Args:
        root: Note、Dictionary 和访问日志所在的 Memory 根目录。
        retriever_factory: 每次真实搜索时创建检索器的可替换工厂。

    Returns:
        已注册工具发现与调用处理器的 MCP Server。
    """
    server = Server("memory")
    store = MemoryStore(root)
    dictionary_path = root / _DICT_L0
    retriever = LazyRetriever(retriever_factory)

    @server.list_tools()
    async def list_tools() -> list[types.Tool]:
        """返回 search、read、outcome 的客户端发现定义。

        search 要求 query，read 要求 uri，outcome 要求 read_id 和固定枚举中的
        outcome。这些 required 声明仅用于客户端发现，服务端不会强制；分发器
        对 search 使用 query=""、top_k=5、include_inactive=False，对 read
        使用 uri=""、search_id=""，对 outcome 使用 read_id=""、
        outcome="unknown"、reason=""。
        """
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
        """分发原始工具请求并返回单段 JSON 文本。

        每次调用都重新读取环境身份和请求 ``_meta``。即使 schema 标记为必填，
        分发器也会为缺失参数补值：search 使用 query=""、top_k=5、
        include_inactive=False，read 使用 uri=""、search_id=""，outcome 使用
        read_id=""、outcome="unknown"、reason=""。它只把 ``top_k`` 转成
        ``int``、``include_inactive`` 按 Python 真值转成 ``bool``，不执行声明
        的 JSON Schema 校验。未知工具返回错误字典。参数转换或处理器抛出的
        ``Exception`` 会记录堆栈，并转换为包含异常 ``repr`` 的内部错误字典。

        结果以 ``ensure_ascii=False`` 编码为一个 ``TextContent``；只要结果字典
        含 ``error`` 键，``isError`` 就为 True。请求字段读取、身份及 ``_meta``
        提取发生在异常捕获之前，结果 JSON 编码也在其后，这些阶段的异常会直接
        传播。

        Args:
            req: 保留工具名、参数和原始 ``_meta`` 的 MCP 请求。

        Returns:
            包含单段 JSON 文本及 ``isError`` 标志的 MCP ServerResult。
        """
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
                    retriever=retriever,
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

    # Server.call_tool 只把 name/arguments 传给回调，会丢弃本模块需要的 _meta。
    server.request_handlers[types.CallToolRequest] = _handle_call_tool
    return server


async def main() -> None:
    """选择 Memory 根目录并运行 stdio MCP server。

    ``MEMORY_ROOT`` 去除首尾空白后非空时，路径只展开 ``~``，不解析或创建目录；
    缺失或全为空白时按项目规则解析默认根目录。函数配置 INFO 日志，创建空通知
    选项和实验能力的初始化参数，然后运行到 stdio 会话结束。启动、传输和服务
    异常直接传播。
    """
    from trowel_py.resource_lifecycle.reporting import report_current_process

    await report_current_process()
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
