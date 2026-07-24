from __future__ import annotations

import logging
import uuid
from pathlib import Path
from typing import Any, Callable

from trowel_py.memory.profile_distill.gate import (
    DistillError,
    GatedDraft,
    parse_and_gate_draft,
)
from trowel_py.memory.profile_distill.prompt import build_distill_prompt
from trowel_py.memory.profile_suggestions import (
    PROFILE_DISTILL_POLICY_VERSION,
    load_suggestions,
)
from trowel_py.memory.sessions_repo import SessionRecord
from trowel_py.memory.store import MemoryStore
from trowel_py.memory.types import Suggestion

logger = logging.getLogger("trowel_py.memory.profile_distill_job")

HostFactory = Callable[[SessionRecord, Path], Any]

_DISTILL_WORKDIR_NAME = "distill-work"
_DRAFT_FILE = "suggestions-draft.json"


def _ensure_distill_workdir(date_str: str, memory_root: Path) -> Path:
    workdir = memory_root.parent / _DISTILL_WORKDIR_NAME / date_str
    workdir.mkdir(parents=True, exist_ok=True)
    return workdir


async def drive_and_gate(
    session: SessionRecord,
    workdir: Path,
    prompt: str,
    *,
    proxy_base_url: str,
    settings_path: Path | str | None,
    host_factory: HostFactory | None,
    date_str: str,
    session_registrar: Any = None,
) -> GatedDraft:
    """驱动 CC 并读取 draft；调用者负责 prompt 输入和 gate 后的落盘位置。

    ``session_registrar=None`` 会写入真实 session registry；隔离重放必须传入
    空 registrar。
    """
    if host_factory is not None:
        host = host_factory(session, workdir)
    else:
        from trowel_py.cc_host.service import CCHost
        from trowel_py.memory.mcp_config import write_mcp_config

        # distill 必须经过代理并保留 settings；独立 kind 阻止自身进入候选队列。
        host = CCHost(
            session_id=uuid.uuid4().hex,
            workdir=str(workdir),
            session_kind="distill",
            proxy_base_url=proxy_base_url,
            settings_path=settings_path,
            mcp_config=str(write_mcp_config()),
            session_registrar=session_registrar,
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
        raise DistillError(
            f"distill agent did not finish cleanly for {session.cc_session_id}"
        )

    draft_path = workdir / _DRAFT_FILE
    if not draft_path.exists():
        raise DistillError(
            f"distill agent produced no {_DRAFT_FILE} for {session.cc_session_id}"
        )
    return parse_and_gate_draft(
        draft_path.read_text(encoding="utf-8"),
        cc_session_id=session.cc_session_id,
        date_str=date_str,
    )


async def run_one_session(
    session: SessionRecord,
    date_str: str,
    memory_root: Path,
    *,
    proxy_base_url: str,
    settings_path: Path | str | None = None,
    host_factory: HostFactory | None = None,
    start_offset: int | None = None,
    end_offset: int | None = None,
) -> list[Suggestion]:
    """提炼单个 session，只使用当前策略建议做去重。"""
    store = MemoryStore(memory_root)
    # 坏队列不能永久阻塞提炼；软降级最多导致模型重复提出已有建议。
    try:
        all_suggestions = load_suggestions(memory_root)
    except ValueError:
        logger.warning(
            "distill: corrupt suggestion queue; deduping against empty"
        )
        all_suggestions = []
    existing = [
        s for s in all_suggestions
        if s.policy_version == PROFILE_DISTILL_POLICY_VERSION
    ]
    prompt = build_distill_prompt(
        session.jsonl_path or "",
        existing,
        store.load_profile(),
        start_offset=start_offset,
        end_offset=end_offset,
    )

    base_workdir = _ensure_distill_workdir(date_str, memory_root)
    workdir = base_workdir / session.cc_session_id
    workdir.mkdir(parents=True, exist_ok=True)

    gated = await drive_and_gate(
        session,
        workdir,
        prompt,
        proxy_base_url=proxy_base_url,
        settings_path=settings_path,
        host_factory=host_factory,
        date_str=date_str,
    )
    logger.info(
        "distill gate %s: %s",
        session.cc_session_id,
        gated.stats.to_log_dict(),
    )
    return list(gated.accepted)
