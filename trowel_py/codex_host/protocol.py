"""JSON-RPC 消息分类与协议常量。

app-server 使用省略 ``"jsonrpc":"2.0"`` 头的 JSON-RPC 2.0。字段和 method 来自
0.144.0 执行 ``codex app-server generate-json-schema --experimental`` 生成的 schema
以及 ``tests/codex_host/fixtures/`` 中的真实录制。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping

# 变更验证版本前必须重新录制真实交互并生成 schema baseline。
SUPPORTED_CODEX_VERSION = "0.144.0"

# 禁用 Codex 原生 memories，由 Trowel 单独负责 memory plane。
APP_SERVER_ARGS: tuple[str, ...] = ("app-server", "--stdio", "--disable", "memories")

# 使用专用名称避免覆盖用户配置的 MCP server；同名时 isolation 检查拒绝启用 memory。
TROWEL_NOTE_SEARCH_SERVER_NAME = "trowel_note_search"

# app-server 通过 ``clientInfo.name`` 识别 Compliance Logs 中的客户端；来源见
# app-server README 的 Initialization 契约。
CLIENT_NAME = "trowel_codex_host"
CLIENT_TITLE = "Trowel Codex Host"
CLIENT_VERSION = "0.1.0"

# 仅记录 baseline schema 公布的 server request；列入集合不等于启用 handler。
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
    RESPONSE = "response"
    NOTIFICATION = "notification"
    SERVER_REQUEST = "server_request"
    INVALID = "invalid"


@dataclass(frozen=True)
class ClientInfo:
    """每次建立连接时发送的 ``initialize.params.clientInfo``。"""

    name: str = CLIENT_NAME
    title: str = CLIENT_TITLE
    version: str = CLIENT_VERSION

    def as_dict(self) -> dict[str, str]:
        return {"name": self.name, "title": self.title, "version": self.version}


def classify_server_message(message: Any) -> MessageKind:
    """按 ``id``、``method``、``result`` 和 ``error`` 将 server 消息归类。"""

    if not isinstance(message, Mapping):
        return MessageKind.INVALID
    has_id = "id" in message
    has_method = isinstance(message.get("method"), str) and bool(message.get("method"))
    if has_id and has_method:
        return MessageKind.SERVER_REQUEST
    if has_method and not has_id:
        return MessageKind.NOTIFICATION
    if has_id and not has_method and ("result" in message or "error" in message):
        # ``result`` 或 ``error`` 用于区分 response 与不能完成 pending request 的裸 id。
        return MessageKind.RESPONSE
    return MessageKind.INVALID
