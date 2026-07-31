"""定义 Profile 提炼来源的运行时无关数据契约。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

ProfileSourceRuntime = Literal["claude_code", "codex"]


@dataclass(frozen=True)
class ProfileJournalSlice:
    """表示一个 journal 文件中供 Profile Agent 查看的一段内容。

    Attributes:
        path: 原始 journal 路径。
        start_offset: 半开字节区间的起点；完整文件从 0 开始。
        end_offset: 半开字节区间的终点；None 表示读到文件末尾。
    """

    path: str
    start_offset: int = 0
    end_offset: int | None = None

    def __post_init__(self) -> None:
        """拒绝空路径、负数起点和空的或反向的字节区间。"""
        if not self.path:
            raise ValueError("profile journal path must not be empty")
        if self.start_offset < 0:
            raise ValueError("profile journal start offset must not be negative")
        if self.end_offset is not None and self.end_offset <= self.start_offset:
            raise ValueError("profile journal end offset must be after start offset")

    @property
    def is_whole_file(self) -> bool:
        """返回当前来源是否覆盖整个文件。"""
        return self.start_offset == 0 and self.end_offset is None


@dataclass(frozen=True)
class ProfileDistillSource:
    """区分 Profile 提炼的历史上下文和本次证据目标。

    ``context`` 只帮助 Agent 理解指代；新建议及其证据只能来自 ``target``。

    Attributes:
        runtime: 被提炼内容来自 Claude Code 还是 Codex。
        source_id: 日志、工作目录、建议来源和处理记录使用的稳定身份。
        context: 本次目标以前的历史内容；可以为空。
        target: 本次必须读取且允许产出建议的新增内容。
        completed_at: 当前目标完成的时间，用于排列待处理队列。
    """

    runtime: ProfileSourceRuntime
    source_id: str
    context: tuple[ProfileJournalSlice, ...]
    target: tuple[ProfileJournalSlice, ...]
    completed_at: str

    def __post_init__(self) -> None:
        """拒绝未知运行时、空身份和没有处理目标的来源。"""
        if self.runtime not in {"claude_code", "codex"}:
            raise ValueError(f"unknown profile source runtime: {self.runtime}")
        if not self.source_id:
            raise ValueError("profile source id must not be empty")
        if not self.target:
            raise ValueError("profile source must contain at least one target")

    @property
    def jsonl_path(self) -> str:
        """返回单文件调用方兼容使用的第一个目标路径。"""
        return self.target[0].path

    @property
    def start_offset(self) -> int | None:
        """返回单文件调用方兼容使用的目标起点。"""
        return self.target[0].start_offset

    @property
    def end_offset(self) -> int | None:
        """返回单文件调用方兼容使用的目标终点。"""
        return self.target[0].end_offset
