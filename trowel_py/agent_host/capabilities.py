"""定义 Agent runtime 对前端公开的版本化能力矩阵。"""

from __future__ import annotations

CURRENT_CAPABILITY_VERSION = 1

# 顺序按通用交互、会话设置、专属展示排列，API 和测试共同冻结该顺序。
CC_CAPABILITIES: tuple[str, ...] = (
    "tools",
    "models",
    "effort",
    "permission",
    "question",
    "interrupt",
    "slash_commands",
    "workflow",
    "tasks",
    "subagents",
    "checkpoint",
    "revert",
    "mcp",
)

CODEX_CAPABILITIES: tuple[str, ...] = (
    "tools",
    "models",
    "effort",
    "permission",
    "sandbox",
    "network_access",
    "approval",
    "interrupt",
    "slash_commands",
    "goal",
    "plan",
    "review",
    "subagents",
    "turn_diff",
    "mcp",
)


def capabilities_for_runtime(runtime: str) -> tuple[str, ...]:
    """返回当前 Trowel 对指定原生 runtime 已实证支持的能力。

    Args:
        runtime: 持久化和 API 使用的 runtime 标识，目前为 ``claude_code`` 或
            ``codex``。

    Raises:
        ValueError: runtime 不是当前支持的标识。
    """

    if runtime == "claude_code":
        return CC_CAPABILITIES
    if runtime == "codex":
        return CODEX_CAPABILITIES
    raise ValueError(f"unsupported runtime: {runtime}")
