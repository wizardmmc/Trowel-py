"""把 Claude Code 字节区间适配为共享 Daily Review 处理单元。"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from trowel_py.memory.activity_dates import ActivityDates, extract_activity_dates
from trowel_py.memory.daily_review.agent import HostFactory
from trowel_py.memory.daily_review.models import ReviewSession
from trowel_py.memory.daily_review.processor import process_review_units
from trowel_py.memory.daily_review.sources import (
    ReviewSource,
    build_claude_review_source,
)
from trowel_py.memory.provenance import (
    CcJsonlSource,
    CompletedSegment,
    DerivationProvenance,
    extract_cc_source_models,
)
from trowel_py.memory.sessions_repo import (
    ClaudeSessionsRepository,
    ClaudePendingSegment,
    ClaudeSessionRecord,
    ReviewRequest,
)
from trowel_py.memory.store import MemoryStore
from trowel_py.memory.types import PersistContext

logger = logging.getLogger("trowel_py.memory.review_job")


@dataclass(frozen=True)
class ClaudeReviewUnit:
    """封装一个 Claude Code transcript 片段的 runtime 专用行为。"""

    segment: ClaudePendingSegment
    repo: ClaudeSessionsRepository
    review_date: str
    review_session_id: str | None

    @property
    def label(self) -> str:
        """返回日志使用的 Claude Code 原生会话 ID。"""
        return self.segment.session.cc_session_id

    @property
    def session(self) -> ReviewSession:
        """返回共享 Agent 所需的宿主无关会话身份。"""
        record = self.segment.session
        return ReviewSession(record.cc_session_id, record.workdir)

    def build_source(self) -> ReviewSource:
        """把已提炼前缀作为上下文，把增量区间作为目标。"""
        return build_claude_review_source(self.segment)

    def activity(self) -> ActivityDates:
        """从目标字节区间提取允许写入 Diary 的活动日期。"""
        record = self.segment.session
        return extract_activity_dates(
            record.jsonl_path,
            self.segment.start,
            self.segment.end,
            last_completed_at=record.last_completed_at,
            registered_at=record.registered_at,
        )

    def build_context(
        self,
        activity: ActivityDates,
        derivation: DerivationProvenance | None,
    ) -> PersistContext:
        """构造 Claude Code 字节来源的持久化上下文。"""
        record = self.segment.session
        trowel_session_ids: tuple[str, ...]
        if self.review_session_id is not None:
            trowel_session_ids = (self.review_session_id,)
        else:
            trowel_session_ids = tuple(
                binding.trowel_session_id
                for binding in self.repo.find_trowels_by_cc(record.cc_session_id)
            )
        return _context_for(
            record,
            self.review_date,
            self.segment.start,
            self.segment.end,
            trowel_session_ids=trowel_session_ids,
            activity_dates=activity.dates,
            date_basis=activity.basis,
            derivation=derivation,
        )

    def advance(self) -> None:
        """推进 Claude Code transcript 的已提炼字节水位。"""
        self.repo.advance_segment(
            self.segment.session.cc_session_id,
            self.segment.end,
            datetime.now().isoformat(),
        )


async def review_claude_segments(
    root: Path,
    date_str: str,
    repo: ClaudeSessionsRepository,
    store: MemoryStore,
    *,
    host_factory: HostFactory | None,
    completed_before: str | None,
    review_request: ReviewRequest | None = None,
    enabled: bool = True,
) -> set[str]:
    """查找 Claude Code 待提炼区间并交给共享处理器。

    nightly 扫描全部满足完成时间条件的用户会话；关闭请求只处理入队时冻结的
    原生会话和字节上界。
    """
    if not enabled:
        return set()
    segments = (
        repo.list_pending_segments(completed_before=completed_before)
        if review_request is None
        else repo.find_pending_for_close_request(review_request)
    )
    logger.info(
        "daily review: %d Claude pending segment(s) (date_str=%s)",
        len(segments),
        date_str,
    )
    units = tuple(
        ClaudeReviewUnit(
            segment=segment,
            repo=repo,
            review_date=date_str,
            review_session_id=(
                review_request.trowel_session_id
                if review_request is not None
                else None
            ),
        )
        for segment in segments
    )
    return await process_review_units(
        root,
        date_str,
        units,
        store,
        host_factory=host_factory,
    )


def _context_for(
    session: ClaudeSessionRecord,
    date_str: str,
    start: int,
    end: int,
    *,
    trowel_session_ids: tuple[str, ...],
    activity_dates: tuple[str, ...],
    date_basis: str,
    derivation: DerivationProvenance | None,
) -> PersistContext:
    """为一个 Claude Code JSONL 字节片段构造持久化上下文。"""
    segment_id = f"{session.cc_session_id}:{start}:{end}"
    resolved_trowel_ids = trowel_session_ids or (
        (session.trowel_session_id,) if session.trowel_session_id else ()
    )
    completed_segment = CompletedSegment(
        segment_id=segment_id,
        host_kind="claude_code",
        native_session_id=session.cc_session_id,
        trowel_session_ids=resolved_trowel_ids,
        session_kind=session.session_kind,
        workdir=session.workdir,
        registered_at=session.registered_at,
        completed_at=session.last_completed_at or "",
        source=CcJsonlSource(
            locator=session.jsonl_path,
            start_offset=start,
            end_offset=end,
        ),
        source_models=extract_cc_source_models(session.jsonl_path, start, end),
    )
    return PersistContext(
        segment_id=segment_id,
        cc_session_id=session.cc_session_id,
        workdir=session.workdir,
        registered_at=session.registered_at,
        review_date=date_str,
        source_jsonl=session.jsonl_path,
        source_start_offset=start,
        source_end_offset=end,
        activity_dates=activity_dates,
        date_basis=date_basis,
        processed_date=datetime.now().date().isoformat(),
        completed_segment=completed_segment,
        derivation=derivation,
    )
