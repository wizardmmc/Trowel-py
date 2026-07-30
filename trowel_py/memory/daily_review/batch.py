"""批量提炼 CC 与 Codex 已完成的会话范围，并维护 Daily 和 Dictionary 派生物。"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import Any

from trowel_py.memory.daily_review.agent import (
    DistillError,
    HostFactory,
    run_one_session,
)
from trowel_py.memory.activity_dates import extract_activity_dates
from trowel_py.memory.draft import procedure_warnings
from trowel_py.memory.dualtrack import audit_draft
from trowel_py.memory.judge import judge_session
from trowel_py.memory.persist import persist_draft
from trowel_py.memory.provenance import (
    CcJsonlSource,
    CompletedSegment,
    DerivationProvenance,
    extract_cc_source_models,
)
from trowel_py.memory.sessions_repo import (
    SessionRecord,
    create_sessions_repository,
    open_sessions_db,
)
from trowel_py.memory.store import MemoryStore
from trowel_py.memory.types import PersistContext

# 日志名属于部署过滤契约，不能跟随 Python 模块路径变化。
logger = logging.getLogger("trowel_py.memory.review_job")


def _resolve_provider(provider: Any) -> Any:
    """返回调用方提供的 provider，或尝试从配置创建默认 provider。

    默认 provider 无法初始化时返回 None，后续 Daily 和 Dictionary 维护会走
    无模型降级路径。

    Args:
        provider: 调用方已创建的模型客户端；为 None 时尝试创建默认客户端。

    Returns:
        可用于 Daily 和 Dictionary 维护的模型客户端，或表示不可用的 None。
    """
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
    completed_before: str | None = None,
) -> None:
    """在调用方持有 review 锁时处理所有符合条件的 CC 与 Codex 片段。

    每个片段只有在 note、Episode、meta 和 completion manifest 全部落盘后才
    推进提炼水位。单个片段提炼失败、草稿日期越界或持久化失败时会保留原
    水位；Codex journal 恢复、judge、Daily 和 Dictionary 维护失败不会撤销
    已落盘事实。

    Args:
        root: 会话数据库和 Memory 产物所在的根目录。
        date_str: 本次 review 的工作目录与持久化记录使用的日期，不限制会话的
            登记日期。
        host_factory: 创建提炼 host 的可选工厂；为 None 时使用真实 host。
        provider: Daily 压缩和 Dictionary 重建使用的模型客户端；为 None 时
            尝试从配置创建默认客户端。
        completed_before: 只处理完成时间严格早于此时间的片段；为 None 时不设
            完成时间上限。
    """
    provider = _resolve_provider(provider)
    try:
        from trowel_py.memory.codex_journal import recover_sealed_codex_turns

        recovered = recover_sealed_codex_turns(root)
        if recovered:
            logger.info("daily review: recovered %d sealed Codex turn(s)", recovered)
    except Exception:  # noqa: BLE001 - Codex 修复失败不能阻断 CC review。
        logger.warning("daily review: Codex journal recovery failed", exc_info=True)
    conn = open_sessions_db(root)
    try:
        repo = create_sessions_repository(conn)
        segments = repo.find_incremental(completed_before=completed_before)
        logger.info(
            "daily review: %d incremental segment(s) (date_str=%s)",
            len(segments),
            date_str,
        )
        store = MemoryStore(root)
        touched_dates: set[str] = set()

        for segment in segments:
            session = segment.session
            derivation: DerivationProvenance | None = None

            def capture_derivation(value: DerivationProvenance) -> None:
                """暂存当前提炼运行的派生记录，供当前片段持久化使用。"""
                nonlocal derivation
                derivation = value

            try:
                draft = await run_one_session(
                    session,
                    date_str,
                    root,
                    host_factory=host_factory,
                    start_offset=segment.start,
                    end_offset=segment.end,
                    derivation_sink=capture_derivation,
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
                # procedure 笔记缺少四要素只告警，不能因此永久卡住提炼水位。
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
                trowel_session_ids=tuple(
                    binding.trowel_session_id
                    for binding in repo.find_trowels_by_cc(session.cc_session_id)
                ),
                activity_dates=activity.dates,
                date_basis=activity.basis,
                processed_date=datetime.now().date().isoformat(),
                derivation=derivation,
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

        from trowel_py.memory.daily_review.codex import review_codex_segments

        touched_dates.update(
            await review_codex_segments(
                root,
                date_str,
                repo,
                store,
                host_factory=host_factory,
                completed_before=completed_before,
            )
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
    """为指定日期生成 Daily，无法压缩时改写可追溯的 fallback。

    fallback 写入失败只记录告警，不中断其他日期的维护。

    Args:
        root: Memory 根目录。
        review_date: 要生成 Daily 的日期，格式为 ``YYYY-MM-DD``。
        provider: Daily 压缩使用的模型客户端；为 None 时直接写 fallback。
    """
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
    """检查 Dictionary 与现有 Note 是否一致，并在可用时重建。

    没有 provider 时只检查漂移并标记 stale。检查或重建抛出异常时只记录告警，
    不撤销已经落盘的 Note。

    Args:
        root: Memory 根目录。
        provider: Dictionary 重建使用的模型客户端；为 None 时不尝试重建。
    """
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
    trowel_session_ids: tuple[str, ...] = (),
    activity_dates: tuple[str, ...] = (),
    date_basis: str = "",
    processed_date: str = "",
    derivation: DerivationProvenance | None = None,
) -> PersistContext:
    """为一个 CC JSONL 字节片段构造持久化上下文和来源记录。

    Args:
        session: 当前片段所属的已注册 CC 会话。
        date_str: 本次 review 写入持久化记录的日期。
        start: 来源 JSONL 半开字节区间的起点。
        end: 来源 JSONL 半开字节区间的终点。
        trowel_session_ids: 与该 CC 会话绑定的 Trowel 会话 ID；为空时回退到
            ``session.trowel_session_id``。
        activity_dates: 当前片段确认的活动日期；优先取 JSONL 事件时间，缺失时
            依次回退到会话完成时间和登记时间；为空表示无法确认。
        date_basis: ``activity_dates`` 使用的时间来源。
        processed_date: 当前片段完成提炼的日期。
        derivation: 本次提炼使用的 runtime、模型和流水线记录。

    Returns:
        同时包含旧持久化字段和 host-neutral ``CompletedSegment`` 的上下文。
    """
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
        processed_date=processed_date,
        completed_segment=completed_segment,
        derivation=derivation,
    )


def _out_of_range_dates(
    diary: tuple,
    activity_dates: tuple[str, ...],
) -> tuple[str, ...]:
    """返回草稿中不属于当前片段活动日期的日期。

    Args:
        diary: 草稿中的 Diary 条目。
        activity_dates: 当前来源片段允许归属的日期。

    Returns:
        按草稿顺序保留的越界日期；没有允许日期时返回草稿中的全部日期。
    """
    # 没有可归属的活动日期时，任何模型生成的日期都不能被当作已验证事实。
    if not activity_dates:
        return tuple(entry.date for entry in diary)
    allowed = set(activity_dates)
    return tuple(entry.date for entry in diary if entry.date not in allowed)
