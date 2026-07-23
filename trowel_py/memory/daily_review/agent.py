from __future__ import annotations

import json
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

from trowel_py.memory.cost import SessionCost, extract_cost_from_jsonl
from trowel_py.memory.draft import Draft, parse_draft, validate_draft
from trowel_py.memory.prompt import build_refine_prompt
from trowel_py.memory.review_workspace import ensure_review_workdir
from trowel_py.memory.sessions_repo import SessionRecord

HostFactory = Callable[[SessionRecord, Path], Any]


class DistillError(Exception):
    """当前 session 无法产出可持久化的 draft。"""


def _cost_text(cost: SessionCost) -> str:
    return f"tokens={cost.total_tokens} turns={cost.num_turns} errors={cost.error_count}"


async def _drive_host(host: Any, prompt: str) -> bool:
    finished = False
    async for event in host.send(prompt):
        if getattr(event, "type", None) == "finished":
            finished = True
    return finished


def _read_draft(draft_path: Path) -> tuple[Draft | None, list[str]]:
    if not draft_path.exists():
        return None, ["draft.json was not created"]
    try:
        draft = parse_draft(draft_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, ValueError, TypeError) as exc:
        return None, [f"draft.json is malformed: {exc}"]
    return draft, validate_draft(draft)


def _revision_prompt(errors: list[str]) -> str:
    details = "\n".join(f"- {error}" for error in errors)
    return (
        "你刚写的 draft.json 被 Python 门禁拒绝。只修改当前工作目录的 "
        "draft.json，按下面具体错误压缩、合并或补全；不要改 memory，不要写其他文件。\n\n"
        f"【门禁错误】\n{details}\n\n"
        "保持原有事实和四列表语义，修好后回复“draft 已修正”。"
    )


def _create_host(
    session: SessionRecord,
    workdir: Path,
    host_factory: HostFactory | None,
) -> Any:
    if host_factory is not None:
        return host_factory(session, workdir)

    from trowel_py.cc_host.service import CCHost
    from trowel_py.memory.mcp_config import write_mcp_config

    # daily review 可脱离 FastAPI 生命周期运行，此处不能依赖应用内代理。
    return CCHost(
        session_id=uuid.uuid4().hex,
        workdir=str(workdir),
        # review 类型阻止提炼会话重新进入用户 session 队列。
        session_kind="review",
        # 注入声明了 memory.search，因此真实 host 必须同时挂载 memory MCP。
        mcp_config=str(write_mcp_config()),
    )


async def run_one_session(
    session: SessionRecord,
    date_str: str,
    memory_root: Path,
    *,
    host_factory: HostFactory | None = None,
    start_offset: int | None = None,
    end_offset: int | None = None,
) -> Draft:
    """驱动一个提炼 session，并返回通过门禁的 draft。

    agent 可读取完整会话作为上下文，但只应为指定的增量区间生成新记忆。首次
    draft 未通过门禁时会在同一 session 内修订一次；仍失败则抛出
    ``DistillError``，由批处理保留水位以便重试。
    """
    cost = extract_cost_from_jsonl(session.jsonl_path)
    prompt = build_refine_prompt(
        session.jsonl_path,
        _cost_text(cost),
        start_offset=start_offset,
        end_offset=end_offset,
    )

    workdir = ensure_review_workdir(date_str, memory_root) / session.cc_session_id
    workdir.mkdir(parents=True, exist_ok=True)
    host = _create_host(session, workdir, host_factory)
    draft_path = workdir / "draft.json"

    try:
        if not await _drive_host(host, prompt):
            raise DistillError(
                f"agent did not finish cleanly for {session.cc_session_id}"
            )
        errors: list[str] = []
        for attempt in range(2):
            draft, errors = _read_draft(draft_path)
            if draft is not None and not errors:
                return draft
            if attempt == 0 and not await _drive_host(host, _revision_prompt(errors)):
                raise DistillError(
                    "agent did not finish draft revision cleanly for "
                    f"{session.cc_session_id}"
                )
        raise DistillError(f"invalid draft for {session.cc_session_id}: {errors}")
    finally:
        close = getattr(host, "close", None)
        if close is not None:
            await close()
