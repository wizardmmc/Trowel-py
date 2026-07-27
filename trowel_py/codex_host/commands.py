"""按已验证的 Codex 协议版本发布 Trowel 原生命令。"""

from __future__ import annotations

import re
from typing import Any

from trowel_py.codex_host.protocol import SUPPORTED_CODEX_VERSION

RESERVED_CODEX_COMMANDS: frozenset[str] = frozenset(
    {"status", "compact", "review", "goal", "diff", "agent"}
)

_COMMAND_TOKEN = re.compile(r"^\s*/([A-Za-z][A-Za-z0-9-]*)(?:\s|$)")

_VALIDATED_COMMANDS: tuple[dict[str, Any], ...] = (
    {
        "name": "status",
        "description": "查看 session、模型、权限、上下文与额度",
        "source": "codex",
        "action": "status",
        "available_while_running": True,
    },
    {
        "name": "compact",
        "description": "压缩当前 thread 的上下文",
        "source": "codex",
        "action": "compact",
        "available_while_running": False,
    },
    {
        "name": "review",
        "description": "选择目标并启动原生代码审查",
        "source": "codex",
        "action": "review",
        "available_while_running": False,
    },
    {
        "name": "goal",
        "description": "打开当前 thread 的 Goal",
        "source": "codex",
        "action": "goal",
        "available_while_running": True,
    },
    {
        "name": "diff",
        "description": "查看当前 turn 的聚合 diff",
        "source": "codex",
        "action": "diff",
        "available_while_running": True,
    },
    {
        "name": "agent",
        "description": "查看并定位当前 thread 的 Subagent",
        "source": "codex",
        "action": "agent",
        "available_while_running": True,
    },
)


def command_roster(version: str | None) -> list[dict[str, Any]]:
    """未知版本不继承旧能力，避免 override 模式把漂移协议暴露给界面。"""

    if version != SUPPORTED_CODEX_VERSION:
        return []
    return [dict(command) for command in _VALIDATED_COMMANDS]


def reserved_command_name(text: str) -> str | None:
    """识别必须由 Trowel 本地处理的首个 slash token。"""

    match = _COMMAND_TOKEN.match(text)
    if match is None:
        return None
    name = match.group(1).lower()
    return name if name in RESERVED_CODEX_COMMANDS else None
