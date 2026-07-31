"""把 Claude Code 完成水位适配为 Profile 提炼候选。"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Sequence

from trowel_py.memory.sessions_repo import ClaudeSessionRecord
from trowel_py.profile.distill.agent import (
    HostFactory,
    _ensure_distill_workdir,
    drive_and_gate,
)
from trowel_py.profile.distill.prompt import build_distill_prompt
from trowel_py.profile.distill.sources.claude import build_claude_distill_source
from trowel_py.profile.distill.state import ProcessedSession
from trowel_py.profile.models import Suggestion
from trowel_py.profile.repository import ProfileRepository
from trowel_py.profile.suggestions import (
    PROFILE_DISTILL_POLICY_VERSION,
    load_suggestions,
)

logger = logging.getLogger(__name__)


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
    repository = ProfileRepository(memory_root)
    # 队列解析或校验错误只关闭去重；I/O 错误仍中止，避免掩盖存储故障。
    try:
        all_suggestions = load_suggestions(memory_root)
    except ValueError:
        logger.warning("distill: corrupt suggestion queue; deduping against empty")
        all_suggestions = []
    existing = [
        suggestion
        for suggestion in all_suggestions
        if suggestion.policy_version == PROFILE_DISTILL_POLICY_VERSION
    ]
    source = build_claude_distill_source(
        session,
        start_offset=start_offset,
        end_offset=end_offset,
    )
    prompt = build_distill_prompt(
        source.jsonl_path,
        existing,
        repository.load_profile(),
        start_offset=source.start_offset,
        end_offset=source.end_offset,
    )

    base_workdir = _ensure_distill_workdir(date_str, memory_root)
    workdir = base_workdir / source.source_id
    workdir.mkdir(parents=True, exist_ok=True)

    gated = await drive_and_gate(
        source.source_id,
        workdir,
        prompt,
        proxy_base_url=proxy_base_url,
        settings_path=settings_path,
        host_factory=host_factory,
        date_str=date_str,
    )
    logger.info(
        "distill gate %s: %s",
        source.source_id,
        gated.stats.to_log_dict(),
    )
    return list(gated.accepted)
