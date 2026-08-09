"""把 Codex turn fragment 适配为共享 Daily Review 处理单元。"""

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
    build_codex_review_source,
)
from trowel_py.memory.provenance import (
    CodexTurnsSource,
    CompletedSegment,
    DerivationProvenance,
    ModelIdentity,
)
from trowel_py.memory.sessions_repo import (
    CodexPendingFragment,
    CodexTurnsRepository,
)
from trowel_py.memory.store import MemoryStore
from trowel_py.memory.types import PersistContext

logger = logging.getLogger("trowel_py.memory.review_job")


@dataclass(frozen=True)
class CodexReviewUnit:
    """封装一个 Codex fragment 的 runtime 专用行为。"""

    fragment: CodexPendingFragment
    repo: CodexTurnsRepository
    review_date: str

    @property
    def label(self) -> str:
        """返回日志使用的 Codex fragment ID。"""
        return self.fragment.fragment_id

    @property
    def session(self) -> ReviewSession:
        """用 thread ID 构造宿主无关会话身份，不伪装成 CC 记录。"""
        first_turn = self.fragment.turns[0]
        return ReviewSession(self.fragment.thread_id, first_turn.workdir)

    def build_source(self) -> ReviewSource:
        """把已提炼旧 turns 作为上下文，把当前 fragment 作为目标。"""
        return build_codex_review_source(self.repo, self.fragment)

    def activity(self) -> ActivityDates:
        """合并 fragment 内各 turn 的活动日期和最弱回退依据。"""
        return _fragment_activity(self.fragment)

    def build_context(
        self,
        activity: ActivityDates,
        derivation: DerivationProvenance | None,
    ) -> PersistContext:
        """构造 Codex turn 来源的持久化上下文。"""
        return _context_for_codex(
            self.fragment,
            self.review_date,
            activity_dates=activity.dates,
            date_basis=activity.basis,
            derivation=derivation,
        )

    def advance(self) -> None:
        """原子推进 fragment 内全部 Codex turns 的提炼水位。"""
        self.repo.advance_fragment(
            self.fragment.thread_id,
            self.fragment.turn_ids,
        )


async def review_codex_segments(
    root: Path,
    date_str: str,
    repo: CodexTurnsRepository,
    store: MemoryStore,
    *,
    host_factory: HostFactory | None,
    completed_before: str | None,
    trowel_session_id: str | None = None,
    enabled: bool = True,
) -> set[str]:
    """领取 Codex 待提炼 fragment 并交给共享处理器。"""
    if not enabled:
        return set()
    fragments = repo.claim_pending_fragments(
        completed_before=completed_before,
        trowel_session_id=trowel_session_id,
    )
    logger.info(
        "daily review: %d Codex pending fragment(s), %d completed turn(s)"
        " (date_str=%s)",
        len(fragments),
        sum(len(fragment.turns) for fragment in fragments),
        date_str,
    )
    units = tuple(
        CodexReviewUnit(fragment=fragment, repo=repo, review_date=date_str)
        for fragment in fragments
    )
    return await process_review_units(
        root,
        date_str,
        units,
        store,
        host_factory=host_factory,
    )


def _context_for_codex(
    fragment: CodexPendingFragment,
    review_date: str,
    *,
    activity_dates: tuple[str, ...],
    date_basis: str,
    derivation: DerivationProvenance | None,
) -> PersistContext:
    """为一个 Codex 待提炼片段构造持久化上下文和来源记录。"""
    first_turn = fragment.turns[0]
    last_turn = fragment.turns[-1]
    source_models = tuple(
        dict.fromkeys(
            ModelIdentity(
                model=turn.model,
                effort=turn.effort,
                provider=turn.provider,
                basis="binding",
            )
            for turn in fragment.turns
            if any((turn.model, turn.effort, turn.provider))
        )
    )
    trowel_session_ids = tuple(
        dict.fromkeys(
            turn.trowel_session_id for turn in fragment.turns if turn.trowel_session_id
        )
    )
    completed_segment = CompletedSegment(
        segment_id=fragment.fragment_id,
        host_kind="codex",
        native_session_id=fragment.thread_id,
        trowel_session_ids=trowel_session_ids,
        session_kind=first_turn.session_kind,
        workdir=first_turn.workdir,
        registered_at=first_turn.registered_at,
        completed_at=last_turn.completed_at or "",
        source=CodexTurnsSource(turn_ids=fragment.turn_ids),
        source_models=source_models,
    )
    return PersistContext(
        segment_id=fragment.fragment_id,
        # 旧持久化接口仍以该字段组织 Episode 路径；规范身份在
        # completed_segment.host_kind/native_session_id 中。
        cc_session_id=fragment.thread_id,
        workdir=first_turn.workdir,
        registered_at=first_turn.registered_at,
        review_date=review_date,
        source_jsonl=first_turn.journal_path,
        activity_dates=activity_dates,
        date_basis=date_basis,
        processed_date=datetime.now().date().isoformat(),
        completed_segment=completed_segment,
        derivation=derivation,
    )


def _fragment_activity(fragment: CodexPendingFragment) -> ActivityDates:
    """合并片段内各 turn 的活动日期，并记录其中最弱的回退依据。"""
    dates: set[str] = set()
    bases: set[str] = set()
    for turn in fragment.turns:
        activity = extract_activity_dates(
            turn.journal_path,
            0,
            0,
            last_completed_at=turn.completed_at,
            registered_at=turn.registered_at,
        )
        dates.update(activity.dates)
        bases.add(activity.basis)
    for basis in ("registered_at", "completed_at", "jsonl_timestamp"):
        if basis in bases:
            return ActivityDates(tuple(sorted(dates)), basis)
    return ActivityDates(tuple(sorted(dates)), "jsonl_timestamp")
