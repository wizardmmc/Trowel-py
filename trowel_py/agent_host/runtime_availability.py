"""探测 Claude Code 与 Codex 的本机 CLI 是否可供 Agent Host 启动。"""

from __future__ import annotations

import shutil
from collections.abc import Callable

from trowel_py.agent_host.binding import Runtime

ExecutableFinder = Callable[[str], str | None]


def detect_runtime_availability(
    find_executable: ExecutableFinder = shutil.which,
) -> dict[Runtime, bool]:
    """返回两种 runtime CLI 在当前进程搜索路径中的安装状态。

    Args:
        find_executable: 按命令名查找可执行文件的函数；测试可传入隔离实现。

    Returns:
        以 runtime 为键的安装状态；找到对应 CLI 时为 True。
    """
    return {
        Runtime.CLAUDE_CODE: find_executable("claude") is not None,
        Runtime.CODEX: find_executable("codex") is not None,
    }
