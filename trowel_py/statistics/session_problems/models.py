"""定义 Statistics 层读取会话问题时使用的内部快照。"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class StoredSessionProblem:
    """保存列表展示字段和质量判断所需的有限内部字段。"""

    trowel_session_id: str
    runtime: str
    closed_at: str
    closed_at_epoch_us: int
    problem_text: str


@dataclass(frozen=True)
class StoredSessionProblemPage:
    """表示同一只读快照中的汇总计数和一页非空问题。"""

    items: tuple[StoredSessionProblem, ...]
    next_cursor: str | None
    reviewed_session_count: int
    problem_count: int
    unavailable_source_count: int
    updated_at: str | None
