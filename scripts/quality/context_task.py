"""让同一公共项目上下文叶在本地宽松、CI 严格模式下运行。"""

from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import Mapping, Sequence


def context_check_arguments(environment: Mapping[str, str]) -> tuple[str, ...]:
    """根据执行环境返回公共项目上下文检查参数。

    Args:
        environment: 任务进程可见的环境变量；存在真值 ``CI`` 时使用严格 Git 快照。

    Returns:
        不经过 shell 展开的 Python 模块命令参数。
    """
    arguments = [sys.executable, "-m", "scripts.shared_context_check"]
    if not environment.get("CI"):
        arguments.append("--allow-untracked")
    return tuple(arguments)


def main(argv: Sequence[str] | None = None) -> int:
    """执行与当前环境匹配的公共项目上下文检查。

    Args:
        argv: 保留给模块入口的参数；当前叶不接受额外参数。
    """
    if argv:
        print("docs.context does not accept arguments", file=sys.stderr)
        return 2
    completed = subprocess.run(context_check_arguments(os.environ), check=False)
    return completed.returncode


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
