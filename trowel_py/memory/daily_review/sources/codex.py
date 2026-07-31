"""把 Codex 已提炼旧 turns 和当前 pending fragment 分成两类来源。"""

from __future__ import annotations

from trowel_py.memory.sessions_repo import (
    CodexPendingFragment,
    CodexTurnsRepository,
)

from .models import JournalSlice, ReviewSource


def build_codex_review_source(
    repo: CodexTurnsRepository,
    fragment: CodexPendingFragment,
) -> ReviewSource:
    """从 sessions registry 取得已提炼旧 turns，并把当前 fragment 作为目标。

    Args:
        repo: 持有 Codex turn 状态和 extracted 水位的 sessions registry。
        fragment: 本次必须作为一个整体提炼和推进水位的 Codex pending fragment。

    Returns:
        按 turn 完成顺序排列的完整 journal 文件来源定义。

    Raises:
        ValueError: 历史 turn 属于其他 thread、尚未提炼，或与当前目标重复。
    """
    extracted_history = repo.list_extracted_before(fragment)
    target_ids = set(fragment.turn_ids)
    for turn in extracted_history:
        if turn.thread_id != fragment.thread_id:
            raise ValueError("Codex review history cannot span threads")
        if turn.extracted_at is None:
            raise ValueError("Codex review history must already be extracted")
        if turn.turn_id in target_ids:
            raise ValueError("Codex review history cannot repeat a target turn")
    return ReviewSource(
        host_kind="codex",
        context=tuple(JournalSlice(turn.journal_path) for turn in extracted_history),
        target=tuple(JournalSlice(turn.journal_path) for turn in fragment.turns),
    )
