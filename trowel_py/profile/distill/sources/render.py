"""把 Profile 来源渲染成 Agent 可执行的路径说明。"""

from __future__ import annotations

from trowel_py.profile.distill.sources.models import (
    ProfileDistillSource,
    ProfileJournalSlice,
)


def _render_slice(source_slice: ProfileJournalSlice) -> str:
    """渲染一个完整 journal 或半开字节区间。"""
    if source_slice.is_whole_file:
        return f"完整文件：{source_slice.path}"
    end = "EOF" if source_slice.end_offset is None else source_slice.end_offset
    return f"{source_slice.path}；半开字节区间 [{source_slice.start_offset}, {end})"


def _render_group(items: tuple[ProfileJournalSlice, ...]) -> str:
    """按来源顺序编号渲染一组 journal。"""
    if not items:
        return "无。"
    return "\n".join(
        f"{index}. {_render_slice(item)}" for index, item in enumerate(items, start=1)
    )


def render_profile_source(source: ProfileDistillSource) -> str:
    """明确展示只供理解的 context 和唯一允许取证的 target。"""
    runtime_rule = (
        "Claude Code：context 和 target 是同一 transcript 中连续的字节区间。"
        if source.runtime == "claude_code"
        else "Codex：各 journal 属于同一 thread，列表顺序就是 turn 完成顺序。"
    )
    return (
        f"- 来源身份：{source.source_id}\n"
        f"- 来源运行时：{source.runtime}\n"
        f"- {runtime_rule}\n\n"
        "【历史上下文（按需查看，只用于理解，不属于本次处理目标）】\n"
        f"{_render_group(source.context)}\n\n"
        "【本次处理目标（必须全部读取）】\n"
        f"{_render_group(source.target)}"
    )
