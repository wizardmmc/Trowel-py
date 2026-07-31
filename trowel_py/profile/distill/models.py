"""定义 Profile 提炼批处理使用的运行时无关身份契约。"""

from __future__ import annotations

from typing import Callable, Protocol

EvidenceValidator = Callable[[str], bool]


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
    """约束不同 runtime 提炼候选共享的身份字段。

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
    def session(self) -> ProfileDistillSessionLike:
        """返回生成 agent 所需的原生会话身份和工作目录。"""
        ...
