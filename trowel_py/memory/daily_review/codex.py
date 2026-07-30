"""提炼已完成的 Codex 轮次，并通过共享持久化链写入 Memory。"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path

from trowel_py.memory.activity_dates import extract_activity_dates
from trowel_py.memory.daily_review.agent import (
    DistillError,
    HostFactory,
    run_one_session,
)
from trowel_py.memory.daily_review.sources import build_codex_review_source
from trowel_py.memory.draft import procedure_warnings
from trowel_py.memory.dualtrack import audit_draft
from trowel_py.memory.judge import judge_session
from trowel_py.memory.persist import persist_draft
from trowel_py.memory.provenance import (
    CodexTurnsSource,
    CompletedSegment,
    DerivationProvenance,
    ModelIdentity,
)
from trowel_py.memory.sessions_repo import (
    CodexPendingFragment,
    SessionRecord,
    SessionsRepository,
)
from trowel_py.memory.store import MemoryStore
from trowel_py.memory.types import PersistContext

logger = logging.getLogger("trowel_py.memory.review_job")


async def review_codex_segments(
    root: Path,
    date_str: str,
    repo: SessionsRepository,
    store: MemoryStore,
    *,
    host_factory: HostFactory | None,
    completed_before: str | None,
) -> set[str]:
    """提炼符合条件的 Codex 轮次，并在持久化成功后推进各轮次水位。

    只处理 user、memory eligible、已有终态且尚未提炼的轮次。journal 缺失、
    提炼失败、草稿日期越界或持久化失败时保留原水位；成功推进水位后运行
    judge，judge 失败不撤销已经落盘的事实。

    Args:
        root: Memory 根目录，用于创建 review 工作目录并运行后续 judge；Codex
            journal 路径来自轮次记录。
        date_str: 本次 review 工作目录和持久化记录使用的日期，不限制轮次的
            登记日期。
        repo: 查询 Codex 轮次并推进提炼水位的会话仓库。
        store: 接收 Note、Episode、meta 和 completion manifest 的 MemoryStore。
        host_factory: 创建提炼 host 的可选工厂；为 None 时使用真实 host。
        completed_before: 只处理完成时间严格早于此时间的轮次；为 None 时不设
            完成时间上限。

    Returns:
        已成功持久化并推进提炼水位的草稿中，Diary 条目涉及的日期集合，供
        调用方重建 Daily。
    """
    segments = repo.find_incremental_codex(completed_before=completed_before)
    logger.info(
        "daily review: %d Codex pending fragment(s), %d completed turn(s)"
        " (date_str=%s)",
        len(segments),
        sum(len(segment.turns) for segment in segments),
        date_str,
    )
    touched_dates: set[str] = set()
    for fragment in segments:
        first_turn = fragment.turns[0]
        try:
            review_source = build_codex_review_source(
                repo,
                fragment,
            )
        except ValueError as exc:
            logger.warning(
                "Codex review source invalid for fragment %s: %s (not advanced)",
                fragment.fragment_id,
                exc,
            )
            continue
        session = SessionRecord(
            cc_session_id=fragment.thread_id,
            trowel_session_id=first_turn.trowel_session_id,
            workdir=first_turn.workdir,
            date=(fragment.turns[-1].completed_at or date_str)[:10],
            jsonl_path=first_turn.journal_path,
            registered_at=first_turn.registered_at,
            last_completed_at=fragment.turns[-1].completed_at,
        )
        derivation: DerivationProvenance | None = None

        def capture_derivation(value: DerivationProvenance) -> None:
            """暂存当前提炼运行的派生记录，供该轮次持久化使用。"""
            nonlocal derivation
            derivation = value

        try:
            draft = await run_one_session(
                session,
                date_str,
                root,
                review_source=review_source,
                host_factory=host_factory,
                derivation_sink=capture_derivation,
            )
        except DistillError as exc:
            logger.warning(
                "Codex distill failed for fragment %s (not advanced): %s",
                fragment.fragment_id,
                exc,
            )
            continue

        activity_dates, date_basis = _fragment_activity(fragment)
        bad_dates = _out_of_range_dates(draft.diary, activity_dates)
        if bad_dates:
            logger.warning(
                "Codex draft diary dates %s outside activity_dates %s for %s "
                "(not advanced)",
                bad_dates,
                activity_dates,
                fragment.fragment_id,
            )
            continue

        audit = audit_draft(draft)
        if not audit.clean:
            logger.warning(
                "dualtrack leaks in Codex %s: %s",
                fragment.fragment_id,
                [(leak.date, leak.signal, leak.snippet) for leak in audit.leaks],
            )
        warnings = procedure_warnings(draft)
        if warnings:
            logger.warning(
                "procedure gaps in Codex %s: %s",
                fragment.fragment_id,
                warnings,
            )

        context = _context_for_codex(
            fragment,
            date_str,
            activity_dates=activity_dates,
            date_basis=date_basis,
            derivation=derivation,
        )
        try:
            report = persist_draft(store, draft, context)
        except (OSError, ValueError) as exc:
            logger.warning(
                "Codex persist failed for %s (not advanced): %s",
                fragment.fragment_id,
                exc,
            )
            continue
        if not report.ok:
            logger.warning(
                "Codex persist incomplete for %s (not advanced)",
                fragment.fragment_id,
            )
            continue

        try:
            repo.advance_codex_extracted_many(
                fragment.thread_id,
                fragment.turn_ids,
            )
        except ValueError as exc:
            logger.warning(
                "Codex fragment watermark failed for %s after persist: %s",
                fragment.fragment_id,
                exc,
            )
            continue
        touched_dates.update(entry.date for entry in draft.diary)
        try:
            await judge_session(
                session,
                date_str,
                root,
                host_factory=host_factory,
                segment_id=context.segment_id,
                review_source=review_source,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Codex judge raised for %s (isolated): %s",
                fragment.fragment_id,
                exc,
            )
    return touched_dates


def _context_for_codex(
    fragment: CodexPendingFragment,
    review_date: str,
    *,
    activity_dates: tuple[str, ...],
    date_basis: str,
    derivation: DerivationProvenance | None,
) -> PersistContext:
    """为一个 Codex 待提炼片段构造持久化上下文和来源记录。

    来源模型只取轮次首次登记时固化的 binding；模型、推理强度和 provider
    均未知时不创建模型记录。

    Args:
        fragment: 已领取且尚未提炼的 Codex 片段。
        review_date: 本次 review 写入持久化记录的日期。
        activity_dates: 轮次完成时间对应的本地日期；完成时间缺失时使用登记
            时间，均无效时为空。
        date_basis: ``activity_dates`` 使用的时间来源。
        derivation: 本次提炼使用的 runtime、模型和流水线记录。

    Returns:
        包含 Codex turn 来源、binding 模型信息、活动日期和提炼来源的持久化
        上下文。
    """
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


def _fragment_activity(
    fragment: CodexPendingFragment,
) -> tuple[tuple[str, ...], str]:
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
            return tuple(sorted(dates)), basis
    return tuple(sorted(dates)), "jsonl_timestamp"


def _out_of_range_dates(
    diary: tuple,
    activity_dates: tuple[str, ...],
) -> tuple[str, ...]:
    """返回草稿中不属于当前 Codex 轮次活动日期的日期。

    Args:
        diary: 草稿中的 Diary 条目。
        activity_dates: 当前轮次允许归属的日期。

    Returns:
        按草稿顺序保留的越界日期；没有允许日期时返回草稿中的全部日期。
    """
    if not activity_dates:
        return tuple(entry.date for entry in diary)
    allowed = set(activity_dates)
    return tuple(entry.date for entry in diary if entry.date not in allowed)
