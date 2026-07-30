"""把 Claude Code 单文件增量水位转换为 review 上下文和目标区间。"""

from __future__ import annotations

from trowel_py.memory.sessions_repo import IncrementalSegment

from .models import JournalSlice, ReviewSource


def build_claude_review_source(segment: IncrementalSegment) -> ReviewSource:
    """把 transcript 起点以前作为上下文，把新增字节区间作为目标。

    Args:
        segment: Sessions registry 根据 extracted/completed 两个字节水位返回的
            Claude Code 增量区间。

    Returns:
        指向同一 transcript 的来源定义；首次提炼时 context 为空，后续提炼时
        context 为 ``[0, start)``，target 为 ``[start, end)``。
    """
    path = segment.session.jsonl_path
    context = (
        (JournalSlice(path, end_offset=segment.start),) if segment.start > 0 else ()
    )
    return ReviewSource(
        host_kind="claude_code",
        context=context,
        target=(JournalSlice(path, segment.start, segment.end),),
    )
