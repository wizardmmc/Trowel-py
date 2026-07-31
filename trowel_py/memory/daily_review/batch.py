"""编排 CC 与 Codex 的增量提炼，并维护 Daily 和 Dictionary 派生物。"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from trowel_py.memory.daily_review.adapters.claude import review_claude_segments
from trowel_py.memory.daily_review.adapters.codex import review_codex_segments
from trowel_py.memory.daily_review.agent import HostFactory
from trowel_py.memory.sessions_repo import (
    create_sessions_repository,
    open_sessions_db,
)
from trowel_py.memory.store import MemoryStore

# 日志名属于部署过滤契约，不能跟随 Python 模块路径变化。
logger = logging.getLogger("trowel_py.memory.review_job")


def _resolve_provider(provider: Any) -> Any:
    """返回调用方提供的 provider，或尝试从配置创建默认 provider。"""
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
    review_session_id: str | None = None,
) -> None:
    """在调用方持有 review 锁时提炼来源并维护 Memory 派生物。

    batch 只负责恢复日志、选择 runtime、汇总受影响日期和维护派生物。两个
    runtime 如何查找来源、构造 provenance 和推进水位分别位于
    ``adapters/claude.py`` 与 ``adapters/codex.py``；共有的 refine 到
    judge 流程位于 ``processor.py``。
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
        review_request = (
            repo.review_requests.find(review_session_id)
            if review_session_id is not None
            else None
        )
        if review_session_id is not None and review_request is None:
            return

        store = MemoryStore(root)
        touched_dates = await review_claude_segments(
            root,
            date_str,
            repo.claude,
            store,
            host_factory=host_factory,
            completed_before=completed_before,
            review_request=(
                review_request
                if review_request is not None
                and review_request.runtime == "claude_code"
                else None
            ),
            enabled=review_request is None
            or review_request.runtime == "claude_code",
        )
        touched_dates.update(
            await review_codex_segments(
                root,
                date_str,
                repo.codex,
                store,
                host_factory=host_factory,
                completed_before=completed_before,
                trowel_session_id=(
                    review_session_id
                    if review_request is not None and review_request.runtime == "codex"
                    else None
                ),
                enabled=review_request is None or review_request.runtime == "codex",
            )
        )

        if provider is not None and review_request is None:
            from trowel_py.memory.compress import daily_dates_needing_rebuild

            touched_dates.update(daily_dates_needing_rebuild(root))
        for review_date in sorted(touched_dates):
            _compress_or_aggregate(root, review_date, provider)
        _maintain_dictionary(root, provider)
        repo.review_requests.complete_satisfied(
            trowel_session_id=review_session_id,
        )
    finally:
        conn.close()


def _compress_or_aggregate(root: Path, review_date: str, provider: Any) -> None:
    """为指定日期生成 Daily，无法压缩时改写可追溯的 fallback。"""
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
        logger.warning(
            "fallback daily write also failed for %s (isolated; review continues)",
            review_date,
            exc_info=True,
        )


def _maintain_dictionary(root: Path, provider: Any) -> None:
    """检查 Dictionary 与现有 Note 是否一致，并在可用时重建。"""
    try:
        if provider is not None:
            from trowel_py.memory.dictionary import ensure_dictionary_consistent

            result = ensure_dictionary_consistent(root, provider)
        else:
            from trowel_py.memory.dictionary import mark_dictionary_stale_if_drifted

            result = mark_dictionary_stale_if_drifted(root)
    except Exception:  # noqa: BLE001
        logger.warning(
            "dictionary ensure raised (non-fatal; notes kept)",
            exc_info=True,
        )
        result = {"dictionary_status": "stale"}
    if result.get("dictionary_status") == "stale":
        logger.warning("dictionary stale after daily: %s", result.get("check_after"))
