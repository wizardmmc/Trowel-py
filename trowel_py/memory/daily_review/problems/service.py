"""隔离执行并持久化一个关闭会话的复盘问题。"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from trowel_py.memory.daily_review.host_runtime import ReviewHostFactory
from trowel_py.memory.provenance import DerivationProvenance
from trowel_py.memory.sessions_repo import (
    ReviewRequest,
    SessionProblemRecord,
    SessionsRepository,
)

from .agent import run_session_problem_agent
from .prompt import SESSION_PROBLEM_PIPELINE_VERSION
from .sources import build_session_problem_scope

logger = logging.getLogger("trowel_py.memory.review_job")


def _aware_iso(value: str | datetime) -> str:
    """把旧的本地朴素时间和新式带偏移时间统一成带偏移 ISO 文本。"""

    parsed = datetime.fromisoformat(value) if isinstance(value, str) else value
    return parsed.astimezone().isoformat(timespec="seconds")


def _record_from_result(
    request: ReviewRequest,
    *,
    problem_text: str | None,
    source_quality: str,
    reviewed_at: str,
    derivation: DerivationProvenance | None,
) -> SessionProblemRecord:
    """把 Agent 结果或确定性空判转换为稳定持久化记录。"""

    generator = derivation.generator if derivation is not None else None
    return SessionProblemRecord(
        trowel_session_id=request.trowel_session_id,
        runtime=request.runtime,
        closed_at=_aware_iso(request.closed_at or request.requested_at),
        problem_text=problem_text,
        reviewed_at=reviewed_at,
        pipeline_version=SESSION_PROBLEM_PIPELINE_VERSION,
        run_id=derivation.run_id if derivation is not None else "",
        generator_runtime=(
            derivation.generator_runtime if derivation is not None else ""
        ),
        generator_model=generator.model if generator is not None else "",
        generator_effort=generator.effort if generator is not None else "",
        source_quality=source_quality,
    )


async def process_session_problem(
    memory_root: Path,
    date_str: str,
    repo: SessionsRepository,
    request: ReviewRequest,
    *,
    host_factory: ReviewHostFactory | None,
    now_fn: Callable[[], datetime] | None = None,
) -> bool:
    """完成一条会话问题请求，失败时保留请求供后续重试。

    已有完成记录会直接与关闭请求状态对账，不再调用模型。无法可靠定位来源的
    旧请求会保存 ``problem_text=None`` 和 ``source_quality=unavailable``，从而
    明确区分“已检查但没有结果”和“尚未处理”。

    Returns:
        已存在或本次成功保存完成记录时为 True；可重试失败时为 False。
    """

    try:
        existing = repo.session_problems.find(request.trowel_session_id)
        if existing is not None:
            repo.session_problems.save_completed(existing)
            return True

        scope = build_session_problem_scope(repo, request)
        derivation: DerivationProvenance | None = None
        problem_text: str | None = None
        if scope.review_source is not None:
            problem_text, derivation = await run_session_problem_agent(
                scope.session,
                date_str,
                memory_root,
                trowel_session_id=request.trowel_session_id,
                review_source=scope.review_source,
                host_factory=host_factory,
            )
        clock = now_fn or (lambda: datetime.now().astimezone())
        record = _record_from_result(
            request,
            problem_text=problem_text,
            source_quality=scope.source_quality,
            reviewed_at=_aware_iso(clock()),
            derivation=derivation,
        )
        repo.session_problems.save_completed(record)
        return True
    except Exception as exc:  # noqa: BLE001 - 问题失败不能回滚 Memory 水位。
        logger.warning(
            "session problem review failed for %s (%s)",
            request.trowel_session_id,
            type(exc).__name__,
        )
        return False
