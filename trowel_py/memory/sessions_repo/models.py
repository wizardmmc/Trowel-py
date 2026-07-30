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
    """记录 Trowel 托管的一个 Codex turn 及其 Memory 处理状态。

    Attributes:
        thread_id: Codex 原生 thread ID；同一段连续会话中的多个 turn 共享该值。
        turn_id: Codex 原生 turn ID，用于在所属 thread 中唯一标识本轮对话。
        trowel_session_id: 首次记录该 turn 时所在的 Trowel Agent 会话 ID；同一
            Codex thread 恢复后产生的不同 turn 可以对应不同 Trowel 会话。
        workdir: 首次记录该 turn 时 Codex 会话使用的工作目录。
        journal_path: Trowel 为该 turn 单独写入的 normalized event JSONL 路径。
        registered_at: 首次收到该 turn 事件并登记到 sessions registry 的时间。
        status: Codex turn 的当前状态；登记时为 ``"running"``，封口后记录
            ``"completed"``、``"interrupted"`` 或 ``"failed"``。
        completed_at: terminal 事件已经同步到 journal 后记录的完成时间；尚未
            封口时为 None。
        extracted_at: 该 turn 所属 fragment 已完整持久化到 Memory 的时间；尚未
            提炼时为 None。
        model: 首次登记该 turn 时固化的 Codex 模型名；无法确认时为空字符串。
        effort: 首次登记该 turn 时固化的推理强度；无法确认时为空字符串。
        provider: 首次登记该 turn 时固化的模型供应商；无法确认时为空字符串。
        memory_enabled: 创建 Trowel 会话时冻结的 Memory 开关。
        profile_enabled: 创建 Trowel 会话时冻结的画像提炼开关。
        session_kind: Trowel 会话类别；daily review 只领取值为 ``"user"`` 的 turn。
        review_fragment_id: daily review 首次领取该 turn 时写入的片段 ID；空字符串
            表示尚未分组，相同非空值表示这些 turn 必须作为一个整体持久化并推进
            extracted 水位。
    """

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
    review_fragment_id: str = ""


@dataclass(frozen=True)
class CodexPendingFragment:
    """同一 Codex thread 中一次性提炼的一组已封口轮次。

    Attributes:
        fragment_id: 首次领取待提炼轮次时固化的片段 ID。
        turns: 按完成时间排列、属于同一 thread 的一个或多个轮次。
    """

    fragment_id: str
    turns: tuple[CodexTurnRecord, ...]

    def __post_init__(self) -> None:
        """拒绝空片段、跨 thread 片段和重复 turn。"""
        if not self.fragment_id or not self.turns:
            raise ValueError("Codex pending fragment must have an id and turns")
        thread_ids = {turn.thread_id for turn in self.turns}
        if len(thread_ids) != 1:
            raise ValueError("Codex pending fragment cannot span threads")
        turn_ids = self.turn_ids
        if len(turn_ids) != len(set(turn_ids)):
            raise ValueError("Codex pending fragment cannot repeat turns")

    @property
    def thread_id(self) -> str:
        """返回片段所属的 Codex 原生 thread ID。"""
        return self.turns[0].thread_id

    @property
    def turn_ids(self) -> tuple[str, ...]:
        """返回按片段顺序排列的 Codex 原生 turn ID。"""
        return tuple(turn.turn_id for turn in self.turns)

    @property
    def journal_paths(self) -> tuple[str, ...]:
        """返回与 ``turn_ids`` 一一对应的 normalized journal 路径。"""
        return tuple(turn.journal_path for turn in self.turns)
