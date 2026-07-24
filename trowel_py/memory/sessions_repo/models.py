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
    """CCHost 注册 session 和推进完成水位所需的最小接口。"""

    def register(self, rec: SessionRecord) -> None: ...

    def update_completed(
        self,
        cc_session_id: str,
        completed_bytes: int,
        when: str | None = None,
    ) -> None: ...


@dataclass(frozen=True)
class IncrementalSegment:
    """一个尚未提炼的已完成 session 区间。"""

    session: SessionRecord
    start: int
    end: int
