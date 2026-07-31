"""把一个 Codex turn 及其同 thread 历史构造成 Profile 来源。"""

from __future__ import annotations

from collections.abc import Sequence

from trowel_py.memory.sessions_repo import CodexTurnRecord
from trowel_py.profile.distill.sources.models import (
    ProfileDistillSource,
    ProfileJournalSlice,
)


def _turn_order(turn: CodexTurnRecord) -> tuple[str, str, str]:
    """返回 Codex turn 在同一 thread 中的稳定先后顺序。"""
    return (turn.completed_at or "", turn.registered_at, turn.turn_id)


def build_codex_distill_source(
    turn: CodexTurnRecord,
    history: Sequence[CodexTurnRecord],
) -> ProfileDistillSource:
    """把更早用户 turns 作为 context，把当前 turn 作为唯一 target。"""
    if turn.completed_at is None:
        raise ValueError("Codex Profile target must be completed")
    for previous in history:
        if previous.thread_id != turn.thread_id:
            raise ValueError("Codex Profile context cannot span threads")
        if previous.completed_at is None:
            raise ValueError("Codex Profile context must be completed")
        if _turn_order(previous) >= _turn_order(turn):
            raise ValueError("Codex Profile context must precede its target")
    return ProfileDistillSource(
        runtime="codex",
        source_id=f"codex:{turn.thread_id}:{turn.turn_id}",
        context=tuple(
            ProfileJournalSlice(previous.journal_path) for previous in history
        ),
        target=(ProfileJournalSlice(turn.journal_path),),
        completed_at=turn.completed_at,
    )
