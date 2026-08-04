"""定义 Agent MCP 交互委派跨进程共用的错误类型。"""


class InteractiveDelegationError(RuntimeError):
    """表示交互委派的输入无效、操作失败或句柄无效。"""

    pass


class InteractiveDelegationCleanupError(InteractiveDelegationError):
    """表示无法确认子会话已经清理。"""

    pass
