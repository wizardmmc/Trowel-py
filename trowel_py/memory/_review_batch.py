from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import Any

from trowel_py.memory._review_agent import (
    DistillError,
    HostFactory,
    run_one_session,
)
from trowel_py.memory.activity_dates import extract_activity_dates
from trowel_py.memory.draft import procedure_warnings
from trowel_py.memory.dualtrack import audit_draft
from trowel_py.memory.judge import judge_session
from trowel_py.memory.persist import persist_draft
from trowel_py.memory.sessions_repo import (
    SessionRecord,
    create_sessions_repository,
    open_sessions_db,
)
from trowel_py.memory.store import MemoryStore
from trowel_py.memory.types import PersistContext

# 内部拆分不改变原有日志分类，避免部署侧过滤规则失效。
logger = logging.getLogger("trowel_py.memory.review_job")


def _resolve_provider(provider: Any) -> Any:
    if provider is not None:
        return provider
    try:
        from trowel_py.config import load_llm_config
        from trowel_py.llm.client import AnthropicProvider

        return AnthropicProvider(load_llm_config())
    except Exception:
        logger.warning("daily review: no LLM provider; daily degrades to aggregate")
        return None


async def run_daily_review_locked(
    root: Path,
    date_str: str,
    host_factory: HostFactory | None,
    provider: Any,
) -> None:
    provider = _resolve_provider(provider)
    conn = open_sessions_db(root)
    try:
        repo = create_sessions_repository(conn)
        segments = repo.find_incremental()
        logger.info(
            "daily review: %d incremental segment(s) (date_str=%s)",
            len(segments),
            date_str,
        )
        store = MemoryStore(root)
        touched_dates: set[str] = set()

        for segment in segments:
            session = segment.session
            try:
                draft = await run_one_session(
                    session,
                    date_str,
                    root,
                    host_factory=host_factory,
                    start_offset=segment.start,
                    end_offset=segment.end,
                )
            except DistillError as exc:
                logger.warning(
                    "distill failed for %s (skipped, not advanced): %s",
                    session.cc_session_id,
                    exc,
                )
                continue

            activity = extract_activity_dates(
                session.jsonl_path,
                segment.start,
                segment.end,
                last_completed_at=session.last_completed_at,
                registered_at=session.registered_at,
            )
            bad_dates = _out_of_range_dates(draft.diary, activity.dates)
            if bad_dates:
                logger.warning(
                    "draft diary dates %s outside activity_dates %s for %s "
                    "(skipped, not advanced)",
                    bad_dates,
                    activity.dates,
                    session.cc_session_id,
                )
                continue

            touched_dates.update(entry.date for entry in draft.diary)
            audit = audit_draft(draft)
            if not audit.clean:
                logger.warning(
                    "dualtrack leaks in %s: %s",
                    session.cc_session_id,
                    [(leak.date, leak.signal, leak.snippet) for leak in audit.leaks],
                )
            warnings = procedure_warnings(draft)
            if warnings:
                # procedure 字段缺口是软门禁，不能让模型输出永久卡住水位。
                logger.warning(
                    "procedure gaps in %s: %s",
                    session.cc_session_id,
                    warnings,
                )

            context = _context_for(
                session,
                date_str,
                segment.start,
                segment.end,
                activity_dates=activity.dates,
                date_basis=activity.basis,
                processed_date=datetime.now().date().isoformat(),
            )
            try:
                report = persist_draft(store, draft, context)
            except (OSError, ValueError) as exc:
                # manifest 未完成时不能推进水位，重跑由持久化层保证幂等。
                logger.warning(
                    "persist failed for %s (skipped, not advanced): %s",
                    session.cc_session_id,
                    exc,
                )
                continue
            if not report.ok:
                logger.warning(
                    "persist incomplete for %s (not advanced)", session.cc_session_id
                )
                continue

            repo.advance_extracted(
                session.cc_session_id,
                segment.end,
                datetime.now().isoformat(),
            )
            try:
                # judge 是水位推进后的附加步骤，失败不能撤销已经落盘的事实。
                await judge_session(
                    session,
                    date_str,
                    root,
                    host_factory=host_factory,
                    segment_id=context.segment_id,
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "judge raised for %s (isolated; review unaffected): %s",
                    session.cc_session_id,
                    exc,
                )

        if provider is not None:
            from trowel_py.memory.compress import daily_dates_needing_rebuild

            touched_dates.update(daily_dates_needing_rebuild(root))
        for review_date in sorted(touched_dates):
            _compress_or_aggregate(root, review_date, provider)
        _maintain_dictionary(root, provider)
    finally:
        conn.close()


def _compress_or_aggregate(root: Path, review_date: str, provider: Any) -> None:
    from trowel_py.memory.compress import compress_daily, write_fallback_daily

    if provider is not None:
        try:
            compress_daily(root, review_date, provider)
            return
        except Exception:
            logger.warning(
                "daily compress raised for %s; writing fallback notice",
                review_date,
                exc_info=True,
            )
    try:
        write_fallback_daily(root, review_date)
    except Exception:  # noqa: BLE001
        # episode 才是事实源，daily 派生失败不应中断其他日期或回滚水位。
        logger.warning(
            "fallback daily write also failed for %s (isolated; review continues)",
            review_date,
            exc_info=True,
        )


def _maintain_dictionary(root: Path, provider: Any) -> None:
    try:
        if provider is not None:
            from trowel_py.memory.dictionary import ensure_dictionary_consistent

            result = ensure_dictionary_consistent(root, provider)
        else:
            from trowel_py.memory.dictionary import mark_dictionary_stale_if_drifted

            result = mark_dictionary_stale_if_drifted(root)
    except Exception:  # noqa: BLE001
        # dictionary 是 notes 的派生索引，维护失败不能回滚已经落盘的 notes。
        logger.warning(
            "dictionary ensure raised (non-fatal; notes kept)",
            exc_info=True,
        )
        result = {"dictionary_status": "stale"}
    if result.get("dictionary_status") == "stale":
        logger.warning("dictionary stale after daily: %s", result.get("check_after"))


def _context_for(
    session: SessionRecord,
    date_str: str,
    start: int,
    end: int,
    *,
    activity_dates: tuple[str, ...] = (),
    date_basis: str = "",
    processed_date: str = "",
) -> PersistContext:
    return PersistContext(
        segment_id=f"{session.cc_session_id}:{start}:{end}",
        cc_session_id=session.cc_session_id,
        workdir=session.workdir,
        registered_at=session.registered_at,
        review_date=date_str,
        source_jsonl=session.jsonl_path,
        source_start_offset=start,
        source_end_offset=end,
        activity_dates=activity_dates,
        date_basis=date_basis,
        processed_date=processed_date,
    )


def _out_of_range_dates(
    diary: tuple,
    activity_dates: tuple[str, ...],
) -> tuple[str, ...]:
    # 没有真实活动日期时，任何模型生成的日期都不能被当作已验证事实。
    if not activity_dates:
        return tuple(entry.date for entry in diary)
    allowed = set(activity_dates)
    return tuple(entry.date for entry in diary if entry.date not in allowed)
