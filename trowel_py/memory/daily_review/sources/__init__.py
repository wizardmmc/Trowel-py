"""集中说明 Claude Code 与 Codex 的 daily review 来源边界。

``claude.py`` 把单个 transcript 拆成历史字节前缀和本次新增区间；
``codex.py`` 把同一 thread 已提炼的旧 turn journals 与当前 pending fragment
分开。refine 和 judge 只接收这里生成的 ``ReviewSource``，不再各自解释路径、
offset 或 runtime 标记。
"""

from .availability import (
    ReviewTargetUnavailable,
    resolve_available_review_source,
)
from .claude import build_claude_review_source
from .codex import build_codex_review_source
from .models import JournalSlice, ReviewSource
from .render import render_review_source

__all__ = [
    "JournalSlice",
    "ReviewSource",
    "ReviewTargetUnavailable",
    "build_claude_review_source",
    "build_codex_review_source",
    "render_review_source",
    "resolve_available_review_source",
]
