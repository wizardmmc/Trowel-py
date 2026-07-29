"""Sessions registry 的数据契约。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class SessionRecord:
    """一个已注册的 Claude Code session。"""

    cc_session_id: str
    workdir: str
    date: str
    trowel_session_id: str = ""
    jsonl_path: str = ""
    registered_at: str = ""
    extracted_at: str | None = None
    session_kind: str = "user"
    last_completed_offset: int | None = None
    last_completed_at: str | None = None
    last_extracted_offset: int | None = None
    last_extracted_at: str | None = None


@dataclass(frozen=True)
class SessionBinding:
    """持久化的 trowel session 到 Claude Code session 映射。"""

    trowel_session_id: str
    cc_session_id: str
    session_kind: str
    workdir: str
    bound_at: str


@runtime_checkable
class SessionRegistrar(Protocol):
    """约束 CCHost 登记会话和记录完成水位所需的同步接口。"""

    def register(self, rec: SessionRecord) -> None:
        """登记一个 Claude Code 会话记录。

        Args:
            rec: 要登记的 Claude Code 会话记录。
        """
        ...

    def update_completed(
        self,
        cc_session_id: str,
        completed_bytes: int,
        when: str | None = None,
    ) -> None:
        """记录 Claude Code 会话可安全提炼到的 transcript 字节位置。

        Args:
            cc_session_id: 要更新的 Claude Code 原生会话 ID。
            completed_bytes: 完整 turn 结束后的 transcript 字节位置。
            when: 水位的记录时间；None 表示由实现生成。
        """
        ...


@dataclass(frozen=True)
class IncrementalSegment:
    """描述 Claude Code transcript 中待提炼的半开字节区间。

    Attributes:
        session: 区间所属的会话及当前水位快照。
        start: 区间起始字节位置，包含该位置。
        end: 区间结束字节位置，不包含该位置。
    """

    session: SessionRecord
    start: int
    end: int


@dataclass(frozen=True)
class CodexTurnRecord:
    """一个 Trowel 托管的 Codex turn 及其 normalized journal。"""

    thread_id: str
    turn_id: str
    trowel_session_id: str
    workdir: str
    journal_path: str
    registered_at: str
    status: str = "running"
    completed_at: str | None = None
    extracted_at: str | None = None
    model: str = ""
    effort: str = ""
    provider: str = ""
    memory_enabled: bool = True
    profile_enabled: bool = True
    session_kind: str = "user"


@dataclass(frozen=True)
class CodexIncrementalSegment:
    """包装一个已经封口且尚未提炼的 Codex 轮次。

    Attributes:
        turn: 满足增量提炼条件的轮次记录。
    """

    turn: CodexTurnRecord
