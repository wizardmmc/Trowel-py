"""为已封口的来源片段准备提炼输入，并驱动 host 生成、校验和修订草稿。"""

from __future__ import annotations

import json
import uuid
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

from trowel_py.memory.cost import SessionCost, extract_cost_from_jsonl
from trowel_py.memory.draft import Draft, parse_draft, validate_draft
from trowel_py.memory.prompt import build_refine_prompt
from trowel_py.memory.provenance import DerivationProvenance, ModelIdentity
from trowel_py.memory.daily_review.workspace import ensure_review_workdir
from trowel_py.memory.daily_review.source_refs import materialize_numbered_source
from trowel_py.memory.sessions_repo import SessionRecord

HostFactory = Callable[[SessionRecord, Path], Any]
DerivationSink = Callable[[DerivationProvenance], None]
_REFINE_PIPELINE_VERSION = 2
_DISTILL_MODEL = "glm-5.1"


class DistillError(Exception):
    """表示当前来源片段未能产出通过门禁的草稿。"""


def _cost_text(cost: SessionCost) -> str:
    """把会话成本格式化为提炼提示中的英文键值行。"""
    return f"tokens={cost.total_tokens} turns={cost.num_turns} errors={cost.error_count}"


async def _drive_host(host: Any, prompt: str) -> bool:
    """发送提示并耗尽 host 事件流，报告其中是否出现 ``finished`` 事件。

    Args:
        host: 提供异步 ``send()`` 方法的提炼 host。
        prompt: 本轮发送给 host 的提示。

    Returns:
        事件流中是否至少出现一次 ``finished`` 事件。
    """
    finished = False
    async for event in host.send(prompt):
        if getattr(event, "type", None) == "finished":
            finished = True
    return finished


def _read_draft(
    draft_path: Path,
    legal_source_refs: set[str],
) -> tuple[Draft | None, list[str]]:
    """解析草稿并检查其结构和来源引用。

    Args:
        draft_path: 提炼 host 应写入的 ``draft.json`` 路径。
        legal_source_refs: 编号来源副本中允许使用的全部 ``Lxxxxxx`` 引用。

    Returns:
        解析后的草稿和门禁错误。文件缺失或内容无法解析时草稿为 None；
        错误列表为空表示草稿通过门禁。
    """
    if not draft_path.exists():
        return None, ["draft.json was not created"]
    try:
        draft = parse_draft(draft_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, ValueError, TypeError) as exc:
        return None, [f"draft.json is malformed: {exc}"]
    return draft, validate_draft(draft, legal_source_refs=legal_source_refs)


def _revision_prompt(errors: list[str]) -> str:
    """生成要求当前提炼 host 按门禁错误修订 ``draft.json`` 的提示。"""
    details = "\n".join(f"- {error}" for error in errors)
    return (
        "你刚写的 draft.json 被 Python 门禁拒绝。只修改当前工作目录的 "
        "draft.json，按下面具体错误压缩、合并或补全；不要改 memory，不要写其他文件。\n\n"
        f"【门禁错误】\n{details}\n\n"
        "保持原有事实、item kind 和 source_refs 语义，修好后回复“draft 已修正”。"
    )


def _create_host(
    session: SessionRecord,
    workdir: Path,
    host_factory: HostFactory | None,
) -> Any:
    """通过注入工厂创建 host，或启动隔离的 Claude Code 提炼会话。

    Args:
        session: 当前来源会话；仅传给注入的 host 工厂。
        workdir: host 读取编号后的来源副本并写入 ``draft.json`` 的工作目录。
        host_factory: 自定义 host 工厂；为 None 时使用固定提炼模型创建真实
            Claude Code host。

    Returns:
        供后续发送提示的提炼 host；对象提供 ``close()`` 时，调用方会在结束时
        关闭它。
    """
    if host_factory is not None:
        return host_factory(session, workdir)

    from trowel_py.cc_host.service import CCHost
    from trowel_py.memory.mcp_config import write_mcp_config

    # daily review 可脱离 FastAPI 生命周期运行，因此在这里直接创建 CCHost。
    return CCHost(
        session_id=uuid.uuid4().hex,
        workdir=str(workdir),
        model=_DISTILL_MODEL,
        # review 类型阻止提炼会话重新进入待提炼的用户 session 队列。
        session_kind="review",
        # 提炼提示要求使用 memory.search，真实 host 因此必须挂载 memory MCP。
        mcp_config=str(write_mcp_config()),
    )


def _derivation_for_host(host: Any) -> DerivationProvenance:
    """根据提炼 host 的配置生成派生来源记录。

    host 未提供模型和推理强度时不记录生成模型；未提供 session ID 时生成新的
    run ID。

    Args:
        host: 本次生成草稿的提炼 host。

    Returns:
        记录提炼流水线、Claude Code runtime 和运行 ID；host 提供模型或推理
        强度时也记录生成模型配置。
    """
    model = getattr(host, "model", None)
    effort = getattr(host, "effort", None)
    generator = (
        ModelIdentity(
            model=str(model or ""),
            effort=str(effort or ""),
            basis="host_config",
        )
        if any(
            isinstance(value, str) and value.strip()
            for value in (model, effort)
        )
        else None
    )
    run_id = getattr(host, "session_id", None)
    return DerivationProvenance(
        pipeline="memory.refine",
        pipeline_version=_REFINE_PIPELINE_VERSION,
        run_id=str(run_id or uuid.uuid4().hex),
        generated_at=datetime.now().astimezone().isoformat(timespec="seconds"),
        generator_runtime="claude_code",
        generator=generator,
    )


async def run_one_session(
    session: SessionRecord,
    date_str: str,
    memory_root: Path,
    *,
    host_factory: HostFactory | None = None,
    start_offset: int | None = None,
    end_offset: int | None = None,
    derivation_sink: DerivationSink | None = None,
    source_runtime: str = "claude_code",
) -> Draft:
    """为一个已封口来源片段生成并校验提炼草稿。

    函数先把指定 JSONL 字节范围过滤为带行号的来源副本，并删除工作目录中的
    旧 ``draft.json``。首次草稿未通过门禁时，会要求同一 host 修订一次；仍然
    无效则抛出 ``DistillError``。已创建的 host 如果提供 ``close()``，无论
    成功或失败都会关闭。

    Args:
        session: 提供来源 JSONL 路径和原生会话 ID 的会话记录。
        date_str: 本次 review 工作目录使用的日期，格式为 ``YYYY-MM-DD``。
        memory_root: 用于定位 review 工作目录的 Memory 根目录。
        host_factory: 自定义提炼 host 工厂；为 None 时创建真实 Claude Code host。
        start_offset: 来源 JSONL 半开字节区间的起点；为 None 时从文件开头读取。
        end_offset: 来源 JSONL 半开字节区间的终点；为 None 时读取到文件末尾。
        derivation_sink: 草稿通过门禁后接收派生来源记录的回调。
        source_runtime: 来源事件的 runtime；设为 ``"codex"`` 时在提示中声明
            编号后的文件来自一个已完成的 Codex turn。

    Returns:
        通过结构和来源引用门禁的提炼草稿。

    Raises:
        DistillError: 无法准备来源、host 未正常结束，或草稿修订后仍未通过门禁。
    """
    workdir = ensure_review_workdir(date_str, memory_root) / session.cc_session_id
    workdir.mkdir(parents=True, exist_ok=True)
    try:
        numbered = materialize_numbered_source(
            Path(session.jsonl_path),
            workdir,
            start_offset=start_offset,
            end_offset=end_offset,
        )
    except (OSError, ValueError) as exc:
        raise DistillError(
            f"cannot materialize source for {session.cc_session_id}: {exc}"
        ) from exc
    cost = extract_cost_from_jsonl(session.jsonl_path)
    prompt = build_refine_prompt(
        str(numbered.path),
        _cost_text(cost),
        start_offset=start_offset,
        end_offset=end_offset,
    )
    if source_runtime == "codex":
        prompt = (
            "【输入运行时】本次 numbered 文件来自 Trowel 持久化的单个 Codex "
            "completed turn normalized event journal；不要补写文件外内容。\n\n"
            + prompt
        )
    draft_path = workdir / "draft.json"
    draft_path.unlink(missing_ok=True)
    host = _create_host(session, workdir, host_factory)

    try:
        if not await _drive_host(host, prompt):
            raise DistillError(
                f"agent did not finish cleanly for {session.cc_session_id}"
            )
        errors: list[str] = []
        for attempt in range(2):
            draft, errors = _read_draft(draft_path, set(numbered.refs))
            if draft is not None and not errors:
                if derivation_sink is not None:
                    derivation_sink(_derivation_for_host(host))
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
