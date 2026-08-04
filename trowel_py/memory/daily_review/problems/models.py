"""定义关闭会话问题分析使用的完整来源范围。"""

from __future__ import annotations

from dataclasses import dataclass

from trowel_py.memory.daily_review.models import ReviewSession
from trowel_py.memory.daily_review.sources import ReviewSource


@dataclass(frozen=True)
class SessionProblemScope:
    """描述一个 Trowel 会话允许用于问题判断的完整来源。

    ``review_source=None`` 表示已确认没有可分析的已封口内容，或旧请求缺少可靠
    边界；调用方按 ``source_quality`` 保存明确空结果，不启动模型。

    Attributes:
        trowel_session_id: 本次关闭请求对应的 Trowel 用户会话 ID。
        runtime: 原会话使用 Claude Code 还是 Codex。
        closed_at: 带 UTC 偏移的关闭时间。
        session: 提供工作目录和隔离 Agent 身份的宿主无关会话。
        review_source: 只包含当前 Trowel 会话完整内容的目标来源。
        native_session_ids: 来源实际涉及的原生会话或 thread ID，仅供内部审计。
        source_quality: reliable 表示边界完整，unavailable 表示旧数据无法可靠定位。
    """

    trowel_session_id: str
    runtime: str
    closed_at: str
    session: ReviewSession
    review_source: ReviewSource | None
    native_session_ids: tuple[str, ...]
    source_quality: str
