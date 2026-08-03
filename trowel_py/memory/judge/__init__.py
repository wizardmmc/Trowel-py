"""会话 memory 使用判效的稳定入口与 agent 生命周期。"""

from __future__ import annotations

import logging
import uuid
from collections import defaultdict
from pathlib import Path
from typing import Any, Callable

from trowel_py.memory.access_log import AccessRecord, read_access_log
from trowel_py.memory.attribution import AttributionIndex
from trowel_py.memory.daily_review.sources import (
    ReviewSource,
    render_review_source,
    resolve_available_review_source,
)
from trowel_py.memory.daily_review.models import ReviewSessionLike
from trowel_py.memory.judge_prompt import build_judge_prompt
from trowel_py.memory.judgements import (
    VALID_ATTRIBUTIONS,
    VALID_OUTCOMES,
    HitJudgement,
    JudgementReport,
    MissJudgement,
    drop_unknown_memory_ids,
    save_judgement_report,
)
from trowel_py.memory.store import MemoryStore

logger = logging.getLogger(__name__)

HostFactory = Callable[[ReviewSessionLike, Path], Any]

_JUDGE_WORKDIR_NAME = "judge-work"
_DRAFT_FILE = "judgement-draft.json"


class JudgeError(Exception):
    """表示事件流未出现 ``finished``，或判效草稿缺失、无法解析。"""


# 草稿解析器处理输入时会从包入口导入 JudgeError；先定义异常，再集中导入子模块实现。
from trowel_py.memory.judge.draft import _coerce_bool, _parse_draft  # noqa: E402
from trowel_py.memory.judge.evidence import (  # noqa: E402
    _dictionary_index,
    _summarize_access_log as _summarize_access_log_impl,
)


def _summarize_access_log(
    root: Path,
    cc_session_id: str,
    index: AttributionIndex,
) -> str:
    """读取访问日志并汇总归属于指定 CC 会话的搜索和正文读取。"""
    return _summarize_access_log_impl(
        root,
        cc_session_id,
        index,
        read_access_log_fn=read_access_log,
    )


def _ensure_judge_workdir(
    date_str: str,
    memory_root: Path,
    cc_session_id: str,
) -> Path:
    """创建并返回 ``judge-work/<date>/<session>`` 判效目录。

    已存在的目录会复用，函数不会清理其中的旧草稿。
    """
    workdir = memory_root.parent / _JUDGE_WORKDIR_NAME / date_str / cc_session_id
    workdir.mkdir(parents=True, exist_ok=True)
    return workdir


async def _judge_session_inner(
    session: ReviewSessionLike,
    review_date: str,
    memory_root: Path,
    host_factory: HostFactory | None,
    review_source: ReviewSource,
    segment_id: str = "",
    activity_dates: tuple[str, ...] = (),
) -> JudgementReport:
    """运行一次会话判效并保存过滤后的报告。

    函数先用会话原始记录、访问证据和 Dictionary 构造提示词，再使用注入的
    host 或新的 ``eval`` CCHost。事件流中至少须出现一次 ``finished``；随后
    读取工作目录中的 ``judgement-draft.json``。目录会复用，但启动 host 前会
    删除旧草稿，避免同一原生会话的不同 segment 相互串用。只要 host 存在
    ``close``，无论发送是否成功都会在 ``finally`` 中等待关闭；若关闭也失败，
    关闭异常会覆盖发送异常。
    解析后会丢弃当前 Memory 中不存在的 Note ID，再保存报告。本函数不隔离
    异常。

    Args:
        session: 要判效的 CC 会话记录。
        review_date: 判效工作目录使用的日期路径段。
        memory_root: 读取证据、Dictionary、Note 并保存报告的 Memory 根目录。
        host_factory: 可选 host 构造器，接收会话和工作目录；为 ``None`` 时用
            随机新 session ID、上述判效目录和 Memory MCP 配置创建 eval
            CCHost。
        review_source: 与 refine 相同的历史上下文和本次判效目标。
        segment_id: 写入报告的可选来源片段 ID。
        activity_dates: 当前来源片段按真实事件或稳定回退时间确认的活动日期。

    Returns:
        已移除未知 Note ID 且完成持久化的判效报告。

    Raises:
        JudgeError: 事件流未出现 ``finished``，或草稿缺失、不是可解析的 JSON
            对象。
    """
    store = MemoryStore(memory_root)
    attribution = AttributionIndex.from_root(memory_root)
    access_summary = _summarize_access_log(
        memory_root,
        session.native_session_id,
        attribution,
    )
    available_source, omitted_context_count = resolve_available_review_source(
        review_source
    )
    if omitted_context_count:
        logger.warning(
            "judge history context incomplete for %s: %d of %d source(s) available",
            session.native_session_id,
            len(available_source.context),
            len(review_source.context),
        )
    prompt = build_judge_prompt(
        render_review_source(available_source),
        access_summary,
        _dictionary_index(store),
    )
    workdir = _ensure_judge_workdir(
        review_date,
        memory_root,
        session.native_session_id,
    )
    draft_path = workdir / _DRAFT_FILE
    draft_path.unlink(missing_ok=True)

    if host_factory is not None:
        host = host_factory(session, workdir)
    else:
        from trowel_py.cc_host.service import CCHost
        from trowel_py.memory.mcp_config import write_mcp_config

        host = CCHost(
            session_id=uuid.uuid4().hex,
            workdir=str(workdir),
            session_kind="eval",
            mcp_config=str(write_mcp_config()),
        )

    finished = False
    try:
        async for event in host.send(prompt):
            if getattr(event, "type", None) == "finished":
                finished = True
    finally:
        close = getattr(host, "close", None)
        if close is not None:
            await close()

    if not finished:
        raise JudgeError(
            f"judge agent did not finish cleanly for {session.native_session_id}"
        )

    if not draft_path.exists():
        raise JudgeError(
            f"judge agent produced no {_DRAFT_FILE} for {session.native_session_id}"
        )
    report = _parse_draft(
        draft_path.read_text(encoding="utf-8"),
        cc_session_id=session.native_session_id,
        segment_id=segment_id,
        activity_dates=activity_dates,
    )
    known_ids = frozenset(
        note.memory_id for _stem, note in store.load_notes_with_id() if note.memory_id
    )
    report = drop_unknown_memory_ids(report, known_ids)
    save_judgement_report(memory_root, report)
    logger.info(
        "judge: %s -> %d hit(s), %d recall-miss",
        session.native_session_id,
        len(report.hits),
        len(report.recall_miss),
    )
    return report


async def judge_session(
    session: ReviewSessionLike,
    review_date: str,
    memory_root: Path,
    *,
    review_source: ReviewSource,
    host_factory: HostFactory | None = None,
    segment_id: str = "",
    activity_dates: tuple[str, ...] = (),
) -> JudgementReport | None:
    """判定单个会话，并把普通异常隔离为 ``None``。

    日志、host、解析、过滤或保存阶段抛出的 ``Exception`` 都会记录警告且不
    影响 review；任务取消等不属于 ``Exception`` 的退出信号仍会传播。

    Args:
        session: 要判效的 CC 会话记录。
        review_date: 仅用于组织判效工作目录的日期路径段。
        memory_root: 读取判效上下文并保存报告的 Memory 根目录。
        review_source: 与 refine 相同的历史上下文和本次判效目标。
        host_factory: 可选 host 构造器，接收会话和判效目录。
        segment_id: 写入报告的可选来源片段 ID。
        activity_dates: 当前来源片段按真实事件或稳定回退时间确认的活动日期。

    Returns:
        成功时返回已保存且过滤未知 Note ID 的报告；任一 ``Exception`` 发生时
        返回 ``None``。
    """
    try:
        return await _judge_session_inner(
            session,
            review_date,
            memory_root,
            host_factory,
            review_source,
            segment_id,
            activity_dates,
        )
    except Exception as exc:  # noqa: BLE001 - 判效是旁路，普通失败不能中断 review。
        logger.warning(
            "judge failed for %s (isolated; review unaffected): %s",
            session.native_session_id,
            exc,
        )
        return None


__all__ = [
    "AccessRecord",
    "AttributionIndex",
    "HostFactory",
    "HitJudgement",
    "JudgeError",
    "MissJudgement",
    "VALID_ATTRIBUTIONS",
    "VALID_OUTCOMES",
    "_DRAFT_FILE",
    "_JUDGE_WORKDIR_NAME",
    "_coerce_bool",
    "_dictionary_index",
    "_ensure_judge_workdir",
    "_judge_session_inner",
    "_parse_draft",
    "_summarize_access_log",
    "build_judge_prompt",
    "defaultdict",
    "drop_unknown_memory_ids",
    "judge_session",
    "read_access_log",
    "save_judgement_report",
]
