"""描述 Claude Code Profile 提炼使用的 JSONL 路径与目标字节区间。"""

from __future__ import annotations

from dataclasses import dataclass

from trowel_py.memory.sessions_repo import ClaudeSessionRecord


@dataclass(frozen=True)
class ClaudeDistillSource:
    """记录 Profile prompt 读取的 Claude Code transcript 范围。

    Attributes:
        source_id: Claude Code 原生会话 ID。
        jsonl_path: 原始 transcript JSONL 路径。
        start_offset: 本次增量起点；``None`` 表示从文件开头开始。
        end_offset: 本次增量终点；``None`` 表示由调用方沿用旧的无上界语义。
    """

    source_id: str
    jsonl_path: str
    start_offset: int | None
    end_offset: int | None


def build_claude_distill_source(
    session: ClaudeSessionRecord,
    *,
    start_offset: int | None,
    end_offset: int | None,
) -> ClaudeDistillSource:
    """从会话记录构造保持现有 prompt 语义的 Claude 来源。

    Args:
        session: 提供原生会话 ID 和 transcript 路径的 Claude Code 记录。
        start_offset: Profile 本次增量起点。
        end_offset: Profile 本次增量终点。

    Returns:
        可直接传给现有 prompt builder 的路径和字节范围。
    """
    return ClaudeDistillSource(
        source_id=session.cc_session_id,
        jsonl_path=session.jsonl_path or "",
        start_offset=start_offset,
        end_offset=end_offset,
    )
