"""管理 daily review 的持久化提炼工作目录，供 Claude Code 和 Codex 来源片段共用。"""
from __future__ import annotations

import logging
import subprocess
from pathlib import Path

from trowel_py.memory.paths import resolve_memory_root

logger = logging.getLogger("trowel_py.memory.review_workspace")

_REVIEW_DIR_NAME = "review-daily-work"


def review_workdir_root(memory_root: Path | None = None) -> Path:
    """返回与 Memory 根目录同级的 review 工作目录路径。

    提炼进程在该目录的日期和会话子目录中运行，以免继承来源项目中的
    agent 指令。本函数只计算路径，不创建目录。

    Args:
        memory_root: Memory 根目录；为 None 时调用 ``resolve_memory_root()``
            读取配置或使用默认目录。

    Returns:
        Memory 根目录父目录下的 ``review-daily-work`` 路径。
    """
    root = memory_root if memory_root is not None else resolve_memory_root()
    return root.parent / _REVIEW_DIR_NAME


def ensure_review_workdir(date_str: str, memory_root: Path | None = None) -> Path:
    """创建指定日期的提炼工作目录，并尝试初始化 Git 仓库。

    若目录中没有 ``.git``，本次调用会执行 ``git init``。命令无法启动时
    只记录告警，返回非零状态时也继续返回目录。

    Args:
        date_str: 直接用于拼接工作目录的日期字符串；约定格式为
            ``YYYY-MM-DD``，本函数不校验。
        memory_root: Memory 根目录；为 None 时调用 ``resolve_memory_root()``
            读取配置或使用默认目录。

    Returns:
        已确保存在的日期工作目录；不保证 Git 初始化成功。

    Raises:
        OSError: 默认 Memory 配置无法读取，或日期工作目录无法创建。
    """
    workdir = review_workdir_root(memory_root) / date_str
    workdir.mkdir(parents=True, exist_ok=True)
    if not (workdir / ".git").exists():
        try:
            subprocess.run(
                ["git", "init"],
                cwd=str(workdir),
                capture_output=True,
                check=False,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            logger.warning("git init failed for %s (ignored): %s", workdir, exc)
    return workdir
