"""管理 daily review 的持久化 CC 工作目录。"""
from __future__ import annotations

import logging
import subprocess
from pathlib import Path

from trowel_py.memory.paths import resolve_memory_root

logger = logging.getLogger("trowel_py.memory.review_workspace")

_REVIEW_DIR_NAME = "review-daily-work"


def review_workdir_root(memory_root: Path | None = None) -> Path:
    """工作目录与 memory root 同级，避免继承用户项目的 agent 指令。"""
    root = memory_root if memory_root is not None else resolve_memory_root()
    return root.parent / _REVIEW_DIR_NAME


def ensure_review_workdir(date_str: str, memory_root: Path | None = None) -> Path:
    """创建按日期持久化的工作目录，并 best-effort 初始化 Git 仓库。"""
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
