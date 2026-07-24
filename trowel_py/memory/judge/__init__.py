"""会话 memory 使用判效的稳定入口与 agent 生命周期。"""

from __future__ import annotations

import logging
import uuid
from collections import defaultdict
from pathlib import Path
from typing import Any, Callable

from trowel_py.memory.access_log import AccessRecord, read_access_log
from trowel_py.memory.attribution import AttributionIndex
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
from trowel_py.memory.sessions_repo import SessionRecord
from trowel_py.memory.store import MemoryStore

logger = logging.getLogger(__name__)

HostFactory = Callable[[SessionRecord, Path], Any]

_JUDGE_WORKDIR_NAME = "judge-work"
_DRAFT_FILE = "judgement-draft.json"


class JudgeError(Exception):
    """judge 未完成或未产生有效草稿。"""


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
    workdir = memory_root.parent / _JUDGE_WORKDIR_NAME / date_str / cc_session_id
    workdir.mkdir(parents=True, exist_ok=True)
    return workdir


async def _judge_session_inner(
    session: SessionRecord,
    review_date: str,
    memory_root: Path,
    host_factory: HostFactory | None,
    segment_id: str = "",
) -> JudgementReport:
    store = MemoryStore(memory_root)
    attribution = AttributionIndex.from_root(memory_root)
    access_summary = _summarize_access_log(
        memory_root,
        session.cc_session_id,
        attribution,
    )
    prompt = build_judge_prompt(
        session.jsonl_path or "",
        access_summary,
        _dictionary_index(store),
    )
    workdir = _ensure_judge_workdir(review_date, memory_root, session.cc_session_id)

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
            f"judge agent did not finish cleanly for {session.cc_session_id}"
        )

    draft_path = workdir / _DRAFT_FILE
    if not draft_path.exists():
        raise JudgeError(
            f"judge agent produced no {_DRAFT_FILE} for {session.cc_session_id}"
        )
    report = _parse_draft(
        draft_path.read_text(encoding="utf-8"),
        cc_session_id=session.cc_session_id,
        segment_id=segment_id,
    )
    known_ids = frozenset(
        note.memory_id for _stem, note in store.load_notes_with_id() if note.memory_id
    )
    report = drop_unknown_memory_ids(report, known_ids)
    save_judgement_report(memory_root, report)
    logger.info(
        "judge: %s -> %d hit(s), %d recall-miss",
        session.cc_session_id,
        len(report.hits),
        len(report.recall_miss),
    )
    return report


async def judge_session(
    session: SessionRecord,
    review_date: str,
    memory_root: Path,
    *,
    host_factory: HostFactory | None = None,
    segment_id: str = "",
) -> JudgementReport | None:
    """判定单个会话；任何失败均隔离为 None。"""
    try:
        return await _judge_session_inner(
            session,
            review_date,
            memory_root,
            host_factory,
            segment_id,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "judge failed for %s (isolated; review unaffected): %s",
            session.cc_session_id,
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
