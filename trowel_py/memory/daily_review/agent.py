"""把已完成的会话范围交给 Agent，并驱动 host 生成、校验和修订草稿。"""

from __future__ import annotations

import json
import logging
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

from trowel_py.memory.cost import SessionCost, extract_cost_from_jsonl
from trowel_py.memory.daily_review.sources import (
    JournalSlice,
    ReviewSource,
    ReviewTargetUnavailable,
    render_review_source,
    resolve_available_review_source,
)
from trowel_py.memory.daily_review.models import ReviewSessionLike
from trowel_py.memory.daily_review.host_runtime import (
    DEFAULT_REVIEW_MODEL,
    ReviewHostFactory,
    create_review_host,
    drive_review_host,
    review_derivation,
)
from trowel_py.memory.daily_review.workspace import ensure_review_workdir
from trowel_py.memory.draft import Draft, parse_draft, validate_draft
from trowel_py.memory.prompt import build_refine_prompt
from trowel_py.memory.provenance import DerivationProvenance
from trowel_py.resource_lifecycle.registry import ResourceRegistry

HostFactory = ReviewHostFactory
DerivationSink = Callable[[DerivationProvenance], None]
_REFINE_PIPELINE_VERSION = 3
logger = logging.getLogger("trowel_py.memory.review_agent")


def resource_aware_host_factory(
    registry: ResourceRegistry,
) -> HostFactory:
    """创建把内部 review CCHost 登记到当前应用账本的工厂。

    Args:
        registry: 当前应用实例的资源账本和进程控制器。
    """

    def create_host(session: ReviewSessionLike, workdir: Path) -> Any:
        """为一段待提炼来源创建带 app owner 账本的隔离 CCHost。

        Args:
            session: 当前来源的 review 会话事实；只用于满足统一 host factory
                契约，不进入内部 CCHost 参数。
            workdir: 本次 Memory 提炼隔离工作目录。
        """

        del session
        from trowel_py.cc_host.service import CCHost
        from trowel_py.memory.mcp_config import write_mcp_config

        return CCHost(
            session_id=uuid.uuid4().hex,
            workdir=str(workdir),
            model=DEFAULT_REVIEW_MODEL,
            session_kind="review",
            mcp_config=str(write_mcp_config()),
            process_controller=registry.process_controller,
            resource_registry=registry,
        )

    return create_host


class DistillError(Exception):
    """表示当前来源片段未能产出通过门禁的草稿。"""


def _derivation_for_host(host: Any) -> DerivationProvenance:
    """保留 refine 层既有测试入口，并委托共享 provenance 构造器。"""

    return review_derivation(
        host,
        pipeline="memory.refine",
        pipeline_version=_REFINE_PIPELINE_VERSION,
    )


def _remove_legacy_numbered_sources(workdir: Path) -> None:
    """删除当前 review 会话目录中旧实现生成的编号来源副本。"""
    for source_copy in workdir.glob("source-*.numbered.jsonl"):
        try:
            source_copy.unlink(missing_ok=True)
        except OSError as exc:
            raise DistillError(
                "cannot remove legacy numbered source from review workdir"
            ) from exc


def _cost_text(cost: SessionCost) -> str:
    """把会话成本格式化为提炼提示中的英文键值行。"""
    return (
        f"tokens={cost.total_tokens} turns={cost.num_turns} errors={cost.error_count}"
    )


def _target_cost(target: tuple[JournalSlice, ...]) -> SessionCost:
    """只累加本次处理目标的近似 token、turn 和错误统计。"""
    costs = tuple(
        extract_cost_from_jsonl(
            source.path,
            start_offset=source.start_offset,
            end_offset=source.end_offset,
        )
        for source in target
    )
    return SessionCost(
        total_tokens=sum(cost.total_tokens for cost in costs),
        num_turns=sum(cost.num_turns for cost in costs),
        error_count=sum(cost.error_count for cost in costs),
    )


def _available_review_source(
    source: ReviewSource,
    session_id: str,
) -> ReviewSource:
    """拒绝缺失目标，并从历史上下文中剔除已经不可读的 journal。

    目标缺失时继续提炼会造成无法追溯或错误推进水位，因此整段失败。历史上下文
    只用于帮助理解；旧文件丢失时记录告警，并让仍完整的目标继续处理。

    Args:
        source: runtime 专用构造器生成的完整 review 来源。
        session_id: 日志中用于定位受影响原生会话的 ID。

    Returns:
        target 保持不变、context 只含当前可读区间的来源定义。

    Raises:
        DistillError: 任一本次处理目标缺失或已短于声明范围。
    """
    try:
        available_source, omitted_context_count = resolve_available_review_source(
            source
        )
    except ReviewTargetUnavailable as exc:
        raise DistillError(f"review target is unavailable for {session_id}") from exc
    if omitted_context_count:
        logger.warning(
            "review history context incomplete for %s: %d of %d source(s) available",
            session_id,
            len(available_source.context),
            len(source.context),
        )
    return available_source


def _read_draft(draft_path: Path) -> tuple[Draft | None, list[str]]:
    """解析草稿并检查其结构。

    Args:
        draft_path: 提炼 host 应写入的 ``draft.json`` 路径。

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
    return draft, validate_draft(draft)


def _revision_prompt(errors: list[str]) -> str:
    """生成要求当前提炼 host 按门禁错误修订 ``draft.json`` 的提示。"""
    details = "\n".join(f"- {error}" for error in errors)
    return (
        "你刚写的 draft.json 被 Python 门禁拒绝。只修改当前工作目录的 "
        "draft.json，按下面具体错误压缩、合并或补全；不要改 memory，不要写其他文件。\n\n"
        f"【门禁错误】\n{details}\n\n"
        "保持原有事实和 item kind，修好后回复“draft 已修正”。"
    )


async def run_one_session(
    session: ReviewSessionLike,
    date_str: str,
    memory_root: Path,
    *,
    review_source: ReviewSource,
    host_factory: HostFactory | None = None,
    derivation_sink: DerivationSink | None = None,
) -> Draft:
    """根据明确划分的历史上下文和处理目标生成并校验提炼草稿。

    runtime 专用来源构造器负责解释 Claude Code offset 或 Codex turn journals；
    本函数只消费统一的 ``ReviewSource``。历史文件缺失时降级为剩余上下文，
    目标文件缺失时拒绝运行。首次草稿未通过门禁时，会要求同一 host 修订一次；
    仍然无效则抛出 ``DistillError``。已创建的 host 如果提供 ``close()``，
    无论成功或失败都会关闭。

    Args:
        session: 提供原生会话 ID 和工作目录身份的会话记录。
        date_str: 本次 review 工作目录使用的日期，格式为 ``YYYY-MM-DD``。
        memory_root: 用于定位 review 工作目录的 Memory 根目录。
        review_source: 已明确区分历史上下文和本次处理目标的 journal 范围。
        host_factory: 自定义提炼 host 工厂；为 None 时创建真实 Claude Code host。
        derivation_sink: 草稿通过门禁后接收派生来源记录的回调。

    Returns:
        通过结构门禁的提炼草稿。

    Raises:
        DistillError: 目标来源不可用、host 未正常结束，或草稿修订后仍未通过
            门禁。
    """
    workdir = ensure_review_workdir(date_str, memory_root) / session.native_session_id
    workdir.mkdir(parents=True, exist_ok=True)
    _remove_legacy_numbered_sources(workdir)
    available_source = _available_review_source(
        review_source,
        session.native_session_id,
    )
    cost = _target_cost(available_source.target)
    prompt = build_refine_prompt(
        render_review_source(available_source),
        _cost_text(cost),
    )
    draft_path = workdir / "draft.json"
    draft_path.unlink(missing_ok=True)
    host = create_review_host(session, workdir, host_factory)

    try:
        if not await drive_review_host(host, prompt):
            raise DistillError(
                f"agent did not finish cleanly for {session.native_session_id}"
            )
        errors: list[str] = []
        for attempt in range(2):
            draft, errors = _read_draft(draft_path)
            if draft is not None and not errors:
                if derivation_sink is not None:
                    derivation_sink(_derivation_for_host(host))
                return draft
            if attempt == 0 and not await drive_review_host(
                host, _revision_prompt(errors)
            ):
                raise DistillError(
                    "agent did not finish draft revision cleanly for "
                    f"{session.native_session_id}"
                )
        raise DistillError(f"invalid draft for {session.native_session_id}: {errors}")
    finally:
        close = getattr(host, "close", None)
        if close is not None:
            await close()
