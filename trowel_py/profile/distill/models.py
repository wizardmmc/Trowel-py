"""定义 Profile 提炼批处理使用的运行时无关身份契约。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Callable, Protocol

if TYPE_CHECKING:
    from trowel_py.profile.distill.sources.models import ProfileDistillSource

EvidenceValidator = Callable[[str], bool]


@dataclass(frozen=True)
class ProfileDistillSession:
    """保存生成 Agent 需要的原生会话身份和工作目录。

    Attributes:
        native_session_id: Claude session ID 或 Codex thread ID。
        workdir: 原用户会话所在的工作目录。
    """

    native_session_id: str
    workdir: str


class ProfileDistillSessionLike(Protocol):
    """约束 Profile 提炼识别原会话所需的宿主无关字段。"""

    @property
    def native_session_id(self) -> str:
        """返回 Claude session ID 或其他 runtime 的原生会话 ID。"""
        ...

    @property
    def workdir(self) -> str:
        """返回原会话的工作目录。"""
        ...


class ProfileDistillCandidate(Protocol):
    """约束不同 runtime 提炼候选交给 batch 的完整共同接口。

    字节 offset、turn ID 等处理位置由各 runtime adapter 自己持有。
    """

    @property
    def runtime(self) -> str:
        """返回来源运行时标识。"""
        ...

    @property
    def label(self) -> str:
        """返回日志使用的稳定来源标识。"""
        ...

    @property
    def source_id(self) -> str:
        """返回建议溯源和工作目录使用的稳定来源身份。"""
        ...

    @property
    def completed_at(self) -> str:
        """返回当前目标的完成时间。"""
        ...

    @property
    def registered_at(self) -> str:
        """返回当前目标的登记时间。"""
        ...

    @property
    def sequence_id(self) -> str:
        """返回失败后不得继续越过的同一来源序列身份。"""
        ...

    @property
    def session(self) -> ProfileDistillSessionLike:
        """返回生成 agent 所需的原生会话身份和工作目录。"""
        ...

    def build_source(self) -> ProfileDistillSource:
        """构造已区分 context 和 target 的 Profile 来源。"""
        ...

    def mark_processed(self, root: Path, *, at: str) -> None:
        """在建议队列持久化后记录当前目标已处理。"""
        ...
