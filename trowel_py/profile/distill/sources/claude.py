"""把 Claude Code transcript 的新增字节构造成 Profile 来源。"""

from __future__ import annotations

from trowel_py.profile.distill.sources.models import (
    ProfileDistillSource,
    ProfileJournalSlice,
)


def build_claude_distill_source(
    *,
    source_id: str,
    jsonl_path: str,
    completed_at: str,
    start_offset: int,
    end_offset: int | None,
) -> ProfileDistillSource:
    """把已处理前缀作为 context，把新增字节区间作为 target。

    Args:
        source_id: Claude Code 原生会话 ID。
        jsonl_path: Claude Code transcript 的原始路径。
        completed_at: 当前目标完成时间；供跨 runtime 候选排序。
        start_offset: Profile 本次增量起点。
        end_offset: Profile 本次增量终点。

    Returns:
        不依赖 sessions 仓储模型的统一 Profile 来源。
    """
    context = (
        (ProfileJournalSlice(jsonl_path, end_offset=start_offset),)
        if start_offset > 0
        else ()
    )
    return ProfileDistillSource(
        runtime="claude_code",
        source_id=source_id,
        context=context,
        target=(
            ProfileJournalSlice(
                jsonl_path,
                start_offset=start_offset,
                end_offset=end_offset,
            ),
        ),
        completed_at=completed_at,
    )
