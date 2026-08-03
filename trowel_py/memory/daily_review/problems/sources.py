"""从关闭请求构造 Claude Code 与 Codex 的完整 Trowel 会话来源。"""

from __future__ import annotations

from trowel_py.memory.daily_review.models import ReviewSession
from trowel_py.memory.daily_review.sources import JournalSlice, ReviewSource
from trowel_py.memory.sessions_repo import ReviewRequest, SessionsRepository

from .models import SessionProblemScope


def build_session_problem_scope(
    repo: SessionsRepository,
    request: ReviewRequest,
) -> SessionProblemScope:
    """用关闭时冻结的身份构造完整且不跨 Trowel 会话的问题来源。

    Args:
        repo: 同时读取关闭请求来源和 runtime 记录的 sessions registry。
        request: 用户关闭会话时持久登记的稳定请求。

    Returns:
        可直接交给问题 Agent 的完整来源；没有可靠内容时来源为 None。

    Raises:
        ValueError: 请求 runtime 未受支持，或来源记录出现跨工作目录异常。
    """

    if request.runtime == "claude_code":
        return _claude_scope(repo, request)
    if request.runtime == "codex":
        return _codex_scope(repo, request)
    raise ValueError(f"unknown review request runtime: {request.runtime}")


def _claude_scope(
    repo: SessionsRepository,
    request: ReviewRequest,
) -> SessionProblemScope:
    """把关闭时冻结的 binding 字节区间转换为完整会话来源。"""

    record = (
        repo.claude.find(request.native_session_id)
        if request.native_session_id
        else None
    )
    workdir = record.workdir if record is not None else ""
    session = ReviewSession(request.trowel_session_id, workdir)
    if (
        record is None
        or request.source_start_offset is None
        or request.source_end_offset is None
    ):
        quality = (
            "reliable"
            if record is not None
            and request.source_start_offset is not None
            and request.source_end_offset is None
            else "unavailable"
        )
        return SessionProblemScope(
            trowel_session_id=request.trowel_session_id,
            runtime=request.runtime,
            closed_at=request.closed_at,
            session=session,
            review_source=None,
            native_session_ids=(request.native_session_id,)
            if request.native_session_id
            else (),
            source_quality=quality,
        )
    if request.source_end_offset <= request.source_start_offset:
        return SessionProblemScope(
            trowel_session_id=request.trowel_session_id,
            runtime=request.runtime,
            closed_at=request.closed_at,
            session=session,
            review_source=None,
            native_session_ids=(request.native_session_id,),
            source_quality="reliable",
        )
    source = ReviewSource(
        host_kind="claude_code",
        context=(),
        target=(
            JournalSlice(
                record.jsonl_path,
                request.source_start_offset,
                request.source_end_offset,
            ),
        ),
    )
    return SessionProblemScope(
        trowel_session_id=request.trowel_session_id,
        runtime=request.runtime,
        closed_at=request.closed_at,
        session=session,
        review_source=source,
        native_session_ids=(request.native_session_id,),
        source_quality="reliable",
    )


def _codex_scope(
    repo: SessionsRepository,
    request: ReviewRequest,
) -> SessionProblemScope:
    """按 Trowel 会话身份选择全部已封口 Codex turns。"""

    turns = repo.codex.list_completed_for_trowel_session(request.trowel_session_id)
    if not turns:
        return SessionProblemScope(
            trowel_session_id=request.trowel_session_id,
            runtime=request.runtime,
            closed_at=request.closed_at,
            session=ReviewSession(request.trowel_session_id, ""),
            review_source=None,
            native_session_ids=(),
            source_quality="reliable",
        )
    workdirs = {turn.workdir for turn in turns}
    if len(workdirs) != 1:
        raise ValueError("one Trowel session cannot span Codex workdirs")
    native_session_ids = tuple(dict.fromkeys(turn.thread_id for turn in turns))
    return SessionProblemScope(
        trowel_session_id=request.trowel_session_id,
        runtime=request.runtime,
        closed_at=request.closed_at,
        session=ReviewSession(request.trowel_session_id, turns[0].workdir),
        review_source=ReviewSource(
            host_kind="codex",
            context=(),
            target=tuple(JournalSlice(turn.journal_path) for turn in turns),
        ),
        native_session_ids=native_session_ids,
        source_quality="reliable",
    )
