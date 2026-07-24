"""Tidy 计划的数据契约。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

OpType = Literal[
    "merge_sources",
    "revise",
    "supersede",
    "contradict",
    "retire",
    "keep",
]


@dataclass(frozen=True)
class TidyOperation:
    """一项待校验、待应用的笔记变更。"""

    type: OpType
    target: str
    reason: str
    evidence: tuple[str, ...] = ()
    expected_revision: str = ""
    canonical: str = ""
    by: str = ""
    new_fields: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class TidyPlan:
    """以来源快照约束的一组 tidy 变更。"""

    plan_id: str
    source_snapshot: dict[str, str]
    operations: tuple[TidyOperation, ...]
    dictionary_rebuild_required: bool = False
    core_candidates: tuple[str, ...] = ()
