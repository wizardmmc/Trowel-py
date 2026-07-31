"""把 Claude Code 完成水位适配为 Profile 提炼候选。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Sequence

from trowel_py.memory.sessions_repo import ClaudeSessionRecord
from trowel_py.profile.distill.agent import HostFactory
from trowel_py.profile.distill.processor import process_profile_source
from trowel_py.profile.distill.sources.claude import build_claude_distill_source
from trowel_py.profile.distill.sources.models import ProfileDistillSource
from trowel_py.profile.distill.state import ProcessedSession, mark_processed
from trowel_py.profile.models import Suggestion


@dataclass(frozen=True)
class ClaudeDistillCandidate:
    """记录一个 Claude Code 会话本次要提炼的字节区间。

    Attributes:
        session: 区间所属的已完成用户会话。
        start_offset: Profile 上次完成提炼的字节位置。
        end_offset: 本轮读取到的最新完成字节位置。
    """

    session: ClaudeSessionRecord
    start_offset: int
    end_offset: int

    @property
    def runtime(self) -> str:
        """返回 Claude Code 的运行时标识。"""
        return "claude_code"

    @property
    def label(self) -> str:
        """返回日志使用的 Claude Code 原生会话 ID。"""
        return self.session.cc_session_id

    @property
    def source_id(self) -> str:
        """返回 Claude Code 原生会话 ID 作为稳定来源身份。"""
        return self.session.cc_session_id

    @property
    def completed_at(self) -> str:
        """返回最新完成字节水位的记录时间。"""
        return self.session.last_completed_at or self.session.registered_at

    @property
    def registered_at(self) -> str:
        """返回 Claude Code 会话的登记时间。"""
        return self.session.registered_at

    @property
    def sequence_id(self) -> str:
        """返回失败时阻断同一 Claude 会话后续增量的身份。"""
        return f"claude:{self.session.cc_session_id}"

    def build_source(self) -> ProfileDistillSource:
        """把已处理前缀和本次新增字节构造成统一来源。"""
        return build_claude_distill_source(
            source_id=self.session.cc_session_id,
            jsonl_path=self.session.jsonl_path or "",
            completed_at=self.completed_at,
            start_offset=self.start_offset,
            end_offset=self.end_offset,
        )

    def mark_processed(self, root: Path, *, at: str) -> None:
        """推进当前 Claude 会话的 Profile 字节水位。"""
        mark_processed(
            root,
            self.session.cc_session_id,
            end_offset=self.end_offset,
            at=at,
        )


def build_claude_backlog(
    sessions: Sequence[ClaudeSessionRecord],
    processed: Mapping[str, ProcessedSession],
) -> list[ClaudeDistillCandidate]:
    """按会话顺序生成仍有新字节的 Profile 提炼候选。

    Args:
        sessions: 已按仓储规则排列的完成用户会话。
        processed: 以 Claude Code 会话 ID 为键的 Profile 独立水位。

    Returns:
        保持输入顺序且结束位置严格大于起始位置的候选列表。
    """
    backlog: list[ClaudeDistillCandidate] = []
    for session in sessions:
        end_offset = session.last_completed_offset or 0
        start_offset = (
            processed[session.cc_session_id].end_offset
            if session.cc_session_id in processed
            else 0
        )
        if end_offset > start_offset:
            backlog.append(
                ClaudeDistillCandidate(
                    session=session,
                    start_offset=start_offset,
                    end_offset=end_offset,
                )
            )
    return backlog


async def run_claude_session(
    session: ClaudeSessionRecord,
    date_str: str,
    memory_root: Path,
    *,
    proxy_base_url: str,
    settings_path: Path | str | None = None,
    host_factory: HostFactory | None = None,
    start_offset: int | None = None,
    end_offset: int | None = None,
) -> list[Suggestion]:
    """提炼一个 Claude Code 会话，只使用当前策略建议做去重。"""
    source = build_claude_distill_source(
        source_id=session.cc_session_id,
        jsonl_path=session.jsonl_path or "",
        completed_at=session.last_completed_at or session.registered_at,
        start_offset=start_offset or 0,
        end_offset=end_offset,
    )
    return await process_profile_source(
        source,
        date_str,
        memory_root,
        proxy_base_url=proxy_base_url,
        settings_path=settings_path,
        host_factory=host_factory,
    )
