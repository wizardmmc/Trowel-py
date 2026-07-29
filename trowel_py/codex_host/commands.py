"""列出由 Trowel 接管的 Codex 原生命令，并识别对应的 slash 输入。"""

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
    """返回指定 Codex 版本已验证的原生命令。

    Args:
        version: 当前 Codex CLI 版本；尚未连接或无法读取时为 None。

    Returns:
        每项命令的名称、界面说明、动作和运行中可用性。未验证的版本返回空列表，
        避免向界面暴露可能已经变化的协议能力。
    """

    if version != SUPPORTED_CODEX_VERSION:
        return []
    return [dict(command) for command in _VALIDATED_COMMANDS]


def reserved_command_name(text: str) -> str | None:
    """返回消息开头由 Trowel 接管的 slash 命令名；没有则返回 None。"""

    match = _COMMAND_TOKEN.match(text)
    if match is None:
        return None
    name = match.group(1).lower()
    return name if name in RESERVED_CODEX_COMMANDS else None
