"""让冻结后的单一可执行文件启动 sidecar 或已声明的 Trowel MCP 服务。"""

from __future__ import annotations

import asyncio
import sys
from collections.abc import Sequence

_AGENT_MCP_MODULE = "trowel_py.agent_mcp.server"
_MEMORY_MCP_MODULE = "trowel_py.memory.mcp_server"
_ALLOWED_MODULES = frozenset({_AGENT_MCP_MODULE, _MEMORY_MCP_MODULE})


def resolve_packaged_module(arguments: Sequence[str]) -> str | None:
    """判断冻结程序应启动 sidecar 还是一个白名单 MCP 模块。

    Args:
        arguments: 冻结可执行文件收到的命令行参数，不包含程序路径。

    Returns:
        无参数时返回 None，表示启动桌面 sidecar；已允许的 ``-m`` 调用返回模块名。

    Raises:
        ValueError: 参数不是受支持的 sidecar 或 MCP 启动形式。
    """
    if not arguments:
        return None
    if (
        len(arguments) == 2
        and arguments[0] == "-m"
        and arguments[1] in _ALLOWED_MODULES
    ):
        return arguments[1]
    raise ValueError("unsupported packaged Trowel entrypoint")


def main(arguments: Sequence[str] | None = None) -> None:
    """按白名单分发冻结程序，并让 MCP 进程保持标准的 stdio 生命周期。

    Args:
        arguments: 测试可显式传入的参数；未提供时读取当前进程命令行。
    """
    raw_arguments = list(sys.argv[1:] if arguments is None else arguments)
    try:
        module = resolve_packaged_module(raw_arguments)
    except ValueError as exc:
        print(f"packaged Trowel entrypoint error: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc

    if module is None:
        from trowel_py.desktop.sidecar import main as run_sidecar

        run_sidecar()
        return

    # 真 Python 处理 ``-m`` 后不会把模块选择参数留给目标模块，冻结入口保持一致。
    sys.argv = [sys.argv[0]]
    if module == _AGENT_MCP_MODULE:
        from trowel_py.agent_mcp.server import main as run_agent_mcp

        asyncio.run(run_agent_mcp())
        return

    from trowel_py.memory.mcp_server import main as run_memory_mcp

    asyncio.run(run_memory_mcp())


if __name__ == "__main__":
    main()
