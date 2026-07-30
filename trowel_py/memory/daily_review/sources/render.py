"""把 review 来源范围渲染为 refine 与 judge 共用的清晰路径说明。"""

from __future__ import annotations

from .models import JournalSlice, ReviewSource


def _render_slice(source_slice: JournalSlice) -> str:
    """返回一个完整文件或半开字节区间的可读说明。"""
    if source_slice.is_whole_file:
        return f"完整文件：{source_slice.path}"
    end = "EOF" if source_slice.end_offset is None else source_slice.end_offset
    return f"{source_slice.path}；半开字节区间 [{source_slice.start_offset}, {end})"


def _render_group(items: tuple[JournalSlice, ...]) -> str:
    """按既定顺序编号渲染一组 journal 区间。"""
    if not items:
        return "无。"
    return "\n".join(
        f"{index}. {_render_slice(item)}" for index, item in enumerate(items, start=1)
    )


def render_review_source(source: ReviewSource) -> str:
    """同时展示历史上下文和本次目标，避免调用方重新解释 runtime 差异。

    Args:
        source: 已由 Claude Code 或 Codex 专用构造器划分好的 review 来源。

    Returns:
        明确标出 context 不属于处理目标、target 是唯一处理对象的路径和范围文本。
    """
    runtime_rule = (
        "Claude Code：context 和 target 是同一 transcript 中连续的字节区间。"
        if source.host_kind == "claude_code"
        else "Codex：各 journal 属于同一 thread，列表顺序就是 turn 完成顺序。"
    )
    return (
        f"{runtime_rule}\n\n"
        "【历史上下文（按需查看，只用于理解，不属于本次处理目标）】\n"
        f"{_render_group(source.context)}\n\n"
        "【本次处理目标（必须全部读取）】\n"
        f"{_render_group(source.target)}"
    )
