"""Daily Review 在不同会话 runtime 之间共享的数据契约。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from trowel_py.memory.activity_dates import ActivityDates
from trowel_py.memory.daily_review.sources import ReviewSource
from trowel_py.memory.provenance import DerivationProvenance
from trowel_py.memory.types import PersistContext


@dataclass(frozen=True)
class ReviewSession:
    """提炼 Agent 所需的宿主无关会话身份。"""

    native_session_id: str
    workdir: str


class ReviewSessionLike(Protocol):
    """允许 runtime 原生记录直接提供 review 所需的两个身份字段。"""

    @property
    def native_session_id(self) -> str:
        """返回 Claude session ID 或 Codex thread ID。"""
        ...

    @property
    def workdir(self) -> str:
        """返回原会话的工作目录。"""
        ...


class ReviewUnit(Protocol):
    """约束一个 runtime 交给共享提炼处理器的最小能力。"""

    @property
    def label(self) -> str:
        """返回日志中稳定标识当前来源片段的文本。"""
        ...

    @property
    def session(self) -> ReviewSessionLike:
        """返回提炼 Agent 使用的原生会话身份和工作目录。"""
        ...

    def build_source(self) -> ReviewSource:
        """划分当前片段的历史上下文和本次处理目标。"""
        ...

    def activity(self) -> ActivityDates:
        """返回当前来源片段允许写入 Diary 的活动日期。"""
        ...

    def build_context(
        self,
        activity: ActivityDates,
        derivation: DerivationProvenance | None,
    ) -> PersistContext:
        """构造当前草稿落盘时使用的来源和派生上下文。"""
        ...

    def advance(self) -> None:
        """在完整持久化成功后推进当前来源片段的提炼水位。"""
        ...
