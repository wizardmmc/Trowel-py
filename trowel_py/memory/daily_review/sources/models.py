"""定义 daily review 可查看的历史范围和本次处理目标。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

HostKind = Literal["claude_code", "codex"]


@dataclass(frozen=True)
class JournalSlice:
    """表示一个原始 journal 文件中允许 Agent 读取的半开字节区间。

    Attributes:
        path: Trowel 或原生 runtime 已经持久化的 journal 绝对路径。
        start_offset: 区间起点，包含该字节；完整文件从 0 开始。
        end_offset: 区间终点，不包含该字节；None 表示读到文件末尾。
    """

    path: str
    start_offset: int = 0
    end_offset: int | None = None

    def __post_init__(self) -> None:
        """拒绝空路径、负 offset 和空的或反向的字节区间。"""
        if not self.path:
            raise ValueError("journal slice path must not be empty")
        if self.start_offset < 0:
            raise ValueError("journal slice start offset must not be negative")
        if self.end_offset is not None and self.end_offset <= self.start_offset:
            raise ValueError("journal slice end offset must be after start offset")

    @property
    def is_whole_file(self) -> bool:
        """返回当前区间是否覆盖从文件开头到末尾。"""
        return self.start_offset == 0 and self.end_offset is None


@dataclass(frozen=True)
class ReviewSource:
    """明确区分一次 daily review 的历史上下文和处理目标。

    ``context`` 只能帮助 Agent 理解指代和前因后果；Note、Episode、成本、
    activity date、provenance 和水位只能来自 ``target``。两个 runtime 的构造
    规则分别位于同目录的 ``claude.py`` 和 ``codex.py``。

    Attributes:
        host_kind: 原始会话来自 Claude Code 还是 Codex。
        context: 已经提炼过、只允许用于理解的 journal 区间；可以为空。
        target: 本次必须处理且允许生成新记忆的 journal 区间；至少包含一项。
    """

    host_kind: HostKind
    context: tuple[JournalSlice, ...]
    target: tuple[JournalSlice, ...]

    def __post_init__(self) -> None:
        """拒绝未知 runtime 和没有处理目标的来源定义。"""
        if self.host_kind not in {"claude_code", "codex"}:
            raise ValueError(f"unknown review source host kind: {self.host_kind}")
        if not self.target:
            raise ValueError("review source must contain at least one target")
