"""JSON-RPC 消息分类与协议常量。

app-server 的消息结构遵循 JSON-RPC 2.0，但省略顶层 ``jsonrpc`` 字段。本模块的
字段与 ``method`` 分类规则以 Codex 0.144.0 生成的 experimental schema 及
``tests/codex_host/fixtures/`` 中的真实录制为依据。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping

# 修改支持的 Codex 版本前，必须重新录制真实交互并生成 schema 基线。
SUPPORTED_CODEX_VERSION = "0.144.0"

# 以 stdio 模式启动 app-server，并关闭其原生 memories；memory 由 Trowel 管理。
APP_SERVER_ARGS: tuple[str, ...] = ("app-server", "--stdio", "--disable", "memories")

# 使用专用名称避免覆盖用户配置；若存在同名 MCP server，隔离检查会拒绝注入。
TROWEL_NOTE_SEARCH_SERVER_NAME = "trowel_note_search"

# app-server 通过 ``clientInfo.name`` 识别 Compliance Logs 中的客户端；来源见
# app-server README 的 Initialization 契约。
CLIENT_NAME = "trowel_codex_host"
CLIENT_TITLE = "Trowel Codex Host"
CLIENT_VERSION = "0.1.0"

# 仅声明 Codex 0.144.0 schema 公布的 server request；列入集合不等于启用 handler。
KNOWN_SERVER_REQUEST_METHODS: frozenset[str] = frozenset(
    {
        "item/commandExecution/requestApproval",
        "item/fileChange/requestApproval",
        "item/permissions/requestApproval",
        "item/tool/requestUserInput",
        "item/tool/call",
        "mcpServer/elicitation/request",
        "applyPatchApproval",
        "execCommandApproval",
        "attestation/generate",
        "currentTime/read",
        "account/chatgptAuthTokens/refresh",
    }
)


class MessageKind(str, Enum):
    """供传输层选择服务端消息处理路径的结构分类。"""

    RESPONSE = "response"
    NOTIFICATION = "notification"
    SERVER_REQUEST = "server_request"
    INVALID = "invalid"


@dataclass(frozen=True)
class ClientInfo:
    """``initialize.params.clientInfo`` 的数据模型。

    Attributes:
        name: Compliance Logs 用于识别客户端实现的稳定名称。
        title: 面向用户展示的客户端名称。
        version: initialize 时上报的 Trowel Codex Host 客户端版本。
    """

    name: str = CLIENT_NAME
    title: str = CLIENT_TITLE
    version: str = CLIENT_VERSION

    def as_dict(self) -> dict[str, str]:
        """返回可直接用作 ``initialize.params.clientInfo`` 的字典。"""

        return {"name": self.name, "title": self.title, "version": self.version}


def classify_server_message(message: Any) -> MessageKind:
    """按 ``id``、``method``、``result`` 和 ``error`` 将服务端消息归类。

    ``id`` 只按键是否存在判断，不校验其值。带 ``id`` 和非空 ``method`` 的映射
    优先视为服务端请求；非空 ``method`` 且没有 ``id`` 的映射视为通知，即使同时
    包含 ``result`` 或 ``error``。带 ``id``、没有非空 ``method`` 且包含 ``result``
    或 ``error`` 的映射视为响应，其余结构无效。
    """

    if not isinstance(message, Mapping):
        return MessageKind.INVALID
    has_id = "id" in message
    has_method = isinstance(message.get("method"), str) and bool(message.get("method"))
    if has_id and has_method:
        return MessageKind.SERVER_REQUEST
    if has_method and not has_id:
        return MessageKind.NOTIFICATION
    if has_id and not has_method and ("result" in message or "error" in message):
        return MessageKind.RESPONSE
    return MessageKind.INVALID
