"""把已完成 Codex 用户 turns 适配为 Profile 提炼候选。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Sequence

from trowel_py.memory.sessions_repo import CodexTurnRecord
from trowel_py.profile.distill.models import ProfileDistillSession
from trowel_py.profile.distill.sources.codex import build_codex_distill_source
from trowel_py.profile.distill.sources.models import ProfileDistillSource
from trowel_py.profile.distill.state import (
    ProcessedCodexTurn,
    mark_codex_processed,
)


@dataclass(frozen=True)
class CodexDistillCandidate:
    """记录一个 Codex turn 及其同 thread 的更早用户 turns。

    Attributes:
        turn: 本次要提炼的已完成用户 turn。
        history: 完成顺序早于目标、只作为 context 的同 thread 用户 turns。
    """

    turn: CodexTurnRecord
    history: tuple[CodexTurnRecord, ...]

    @property
    def runtime(self) -> str:
        """返回 Codex 运行时标识。"""
        return "codex"

    @property
    def label(self) -> str:
        """返回日志使用的稳定来源身份。"""
        return self.source_id

    @property
    def source_id(self) -> str:
        """返回由 thread 和 turn 组成的稳定来源身份。"""
        return f"codex:{self.turn.thread_id}:{self.turn.turn_id}"

    @property
    def completed_at(self) -> str:
        """返回目标 turn 的完成时间。"""
        return self.turn.completed_at or ""

    @property
    def registered_at(self) -> str:
        """返回目标 turn 的登记时间。"""
        return self.turn.registered_at

    @property
    def sequence_id(self) -> str:
        """返回失败后不得越过的 Codex thread 身份。"""
        return f"codex:{self.turn.thread_id}"

    @property
    def session(self) -> ProfileDistillSession:
        """用 thread ID 和原工作目录构造宿主无关会话身份。"""
        return ProfileDistillSession(
            native_session_id=self.turn.thread_id,
            workdir=self.turn.workdir,
        )

    def build_source(self) -> ProfileDistillSource:
        """把历史 turns 作为 context，把当前 turn 作为 target。"""
        return build_codex_distill_source(self.turn, self.history)

    def mark_processed(self, root: Path, *, at: str) -> None:
        """记录当前 Codex turn 已完成 Profile 提炼。"""
        mark_codex_processed(
            root,
            self.turn.thread_id,
            self.turn.turn_id,
            at=at,
        )


def build_codex_backlog(
    turns: Sequence[CodexTurnRecord],
    processed: Mapping[tuple[str, str], ProcessedCodexTurn],
) -> list[CodexDistillCandidate]:
    """按仓储顺序构造尚未完成 Profile 提炼的 turn 候选。

    每个候选保存同 thread 中所有更早完成的用户 turns 作为 context。Memory
    水位、fragment 和 Profile 注入开关都不参与筛选。
    """
    history_by_thread: dict[str, list[CodexTurnRecord]] = {}
    backlog: list[CodexDistillCandidate] = []
    for turn in turns:
        if turn.completed_at is None or turn.session_kind != "user":
            continue
        history = history_by_thread.setdefault(turn.thread_id, [])
        if (turn.thread_id, turn.turn_id) not in processed:
            backlog.append(
                CodexDistillCandidate(
                    turn=turn,
                    history=tuple(history),
                )
            )
        history.append(turn)
    return backlog
