"""为周级或月级范围构造 Tidy 计划，不直接修改 Note。"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from trowel_py.llm.client import LLMProvider
from trowel_py.memory.store import MemoryStore

from .models import TidyOperation, TidyPlan

_VALID_OP_TYPES = {
    "merge_sources",
    "revise",
    "supersede",
    "contradict",
    "retire",
    "keep",
}

_TIDY_SYS = (
    "你是记忆整理器。读本周新笔记 + 现有笔记索引 + 冲突的旧笔记，产出整理计划。"
    "operation 类型：merge_sources（同主题合并，target 合入 canonical）/ supersede（新结论取代旧，target 被 by 取代）/ "
    "contradict（旧结论被证伪）/ retire（过时退场）/ keep（保留不动）。"
    "每项带 reason。target/canonical/by 都填 memory_id（只从给定的里选）。"
    '输出 JSON: {"operations":[{"type":"...","target":"<mid>","reason":"...","canonical":"<mid>","by":"<mid>"}]}。'
    '只输出 JSON，不要解释。若无操作，输出 {"operations":[]}。'
)


def _note_in_iso_week(date_str: str, iso_year: int, iso_week: int) -> bool:
    """判断日期文本是否位于指定 ISO 周。

    日期为空或不是合法 ISO 日期时返回 ``False``。

    Args:
        date_str: 待判断的 ISO 日期文本。
        iso_year: 目标 ISO 年。
        iso_week: 目标 ISO 周序号。

    Returns:
        日期合法且属于目标周时为 ``True``。
    """
    from trowel_py.memory.compress import _in_iso_week

    return _in_iso_week(date_str, iso_year, iso_week)


def build_tidy_plan(
    root: Path | str,
    iso_week: str,
    provider: LLMProvider,
    *,
    plan_id: str | None = None,
) -> TidyPlan:
    """根据指定周内新建或更新的 Note 生成整理计划。

    只有 ``memory_id`` 非空，且 ``created`` 或 ``updated`` 落入目标周的 Note
    才进入模型上下文。范围内没有 Note 时不调用模型。自定义 ``plan_id`` 为空
    时仍使用 ``weekly-{iso_week}``。

    Args:
        root: 记忆目录。
        iso_week: 目标 ISO 周，例如 ``2026-W28``。
        provider: 生成计划的模型提供者。
        plan_id: 可选计划 ID。

    Returns:
        包含所有具有非空 ``memory_id`` 的 Note 的 ``content_hash`` 快照，以及
        经已知 ID 过滤的操作。

    Raises:
        ValueError: ``iso_week`` 无法解析为年和周。
    """
    from trowel_py.memory.compress import _parse_iso_week

    iso_year, iso_week_num = _parse_iso_week(iso_week)

    def in_scope(date_str: str) -> bool:
        """判断 Note 日期是否属于本次计划的 ISO 周。"""
        return _note_in_iso_week(date_str, iso_year, iso_week_num)

    return _build_plan_for_scope(
        root, plan_id or f"weekly-{iso_week}", provider, in_scope
    )


def build_monthly_plan(
    root: Path | str,
    month: str,
    provider: LLMProvider,
    *,
    plan_id: str | None = None,
) -> TidyPlan:
    """为 ``created`` 或 ``updated`` 以目标月份文本开头的 Note 生成整理计划。

    ``month`` 不做格式校验，范围判断只使用字符串前缀。只有 ``memory_id``
    非空，且 ``created`` 或 ``updated`` 落入目标范围的 Note 才进入模型上下文。
    范围内没有 Note 时不调用模型；自定义 ``plan_id`` 为空时仍使用
    ``monthly-{month}``。

    Args:
        root: 记忆目录。
        month: 用于匹配 Note 日期前缀的月份文本。
        provider: 生成计划的模型提供者。
        plan_id: 可选计划 ID。

    Returns:
        包含所有具有非空 ``memory_id`` 的 Note 的 ``content_hash`` 快照，以及
        经已知 ID 过滤的操作。
    """

    def in_scope(date_str: str) -> bool:
        """判断非空 Note 日期是否以目标月份文本开头。"""
        return bool(date_str) and date_str.startswith(month)

    return _build_plan_for_scope(
        root, plan_id or f"monthly-{month}", provider, in_scope
    )


def _build_plan_for_scope(
    root: Path | str,
    plan_id: str,
    provider: LLMProvider,
    in_scope: Any,
) -> TidyPlan:
    """收集规划上下文，并过滤模型引用的未知记忆 ID。

    模型上下文包括范围内 Note、``conflicts_with`` 与文件 stem 匹配的 Note，
    以及 ``dictionary-L1`` 下的 Markdown 文件。这里仅过滤不存在的 ``target``
    以及操作所需的 ``canonical`` / ``by``；``revise`` 字段白名单和订正链成环
    由 ``validate_plan`` 校验，``content_hash`` 是否过期由 ``apply_plan`` 校验。

    Args:
        root: 记忆目录。
        plan_id: 写入返回计划的 ID。
        provider: 生成操作 JSON 的模型提供者。
        in_scope: 接收 Note 日期并返回是否纳入本期的函数。

    Returns:
        所有具有非空 ``memory_id`` 的 Note 的 ``content_hash`` 快照及过滤后的
        操作；范围为空时操作元组为空且不调用模型。
    """
    root_path = Path(root)
    store = MemoryStore(root_path)
    all_with_id = store.load_notes_with_id()
    scope_notes = [
        (stem, note)
        for stem, note in all_with_id
        if note.memory_id and (in_scope(note.created) or in_scope(note.updated))
    ]
    snapshot = {
        note.memory_id: note.content_hash
        for _stem, note in all_with_id
        if note.memory_id
    }
    if not scope_notes:
        return TidyPlan(plan_id=plan_id, source_snapshot=snapshot, operations=())

    conflict_stems = {
        conflict for _stem, note in scope_notes for conflict in note.conflicts_with
    }
    conflict_notes = [
        (stem, note) for stem, note in all_with_id if stem in conflict_stems
    ]

    l1_dir = root_path / "dictionary-L1"
    l1_text = ""
    if l1_dir.exists():
        l1_text = "\n\n".join(
            path.read_text(encoding="utf-8") for path in sorted(l1_dir.glob("*.md"))
        )

    def _block(note: Any) -> str:
        """渲染一条模型上下文，正文最多保留前 500 个字符。"""
        return (
            f"[{note.memory_id}] {note.title} — {note.summary}\nbody: {note.body[:500]}"
        )

    scope_block = "\n\n".join(_block(note) for _stem, note in scope_notes)
    conflict_block = "\n\n".join(_block(note) for _stem, note in conflict_notes)
    user = (
        f"本期新/改笔记：\n{scope_block}\n\n"
        f"现有笔记索引（L1，发现跨期重复用）：\n{l1_text}\n\n"
        f"冲突的旧笔记（订正候选）：\n{conflict_block}\n\n"
        "输出 operations JSON。"
    )
    raw = provider.complete(_TIDY_SYS, user)
    ops = _parse_operations(raw)
    known_ids = set(snapshot)
    valid_ops: list[TidyOperation] = []
    for op in ops:
        if op.target not in known_ids:
            continue
        if op.type == "merge_sources" and op.canonical not in known_ids:
            continue
        if op.type in ("supersede", "contradict") and op.by not in known_ids:
            continue
        valid_ops.append(op)
    return TidyPlan(
        plan_id=plan_id,
        source_snapshot=snapshot,
        operations=tuple(valid_ops),
    )


def _parse_operations(raw: str) -> tuple[TidyOperation, ...]:
    """从模型文本中解析候选整理操作。

    解析器贪婪截取第一个 ``{`` 到最后一个 ``}``。无对象片段或 JSON 解码失败
    时返回空元组；未知操作类型、空目标和非对象操作会被跳过。JSON 解码成功后，
    若顶层不是对象、``operations`` 不可迭代、``type`` 不可哈希，或
    ``revise.new_fields`` 无法转换为字典，相关异常会直接传播。

    Args:
        raw: 模型返回文本。

    Returns:
        保持模型顺序的候选操作。证据和期望 revision 不从模型结果读取。
    """
    match = re.search(r"\{.*\}", raw, re.DOTALL)
    if not match:
        return ()
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError:
        return ()
    out: list[TidyOperation] = []
    for op in data.get("operations", []):
        if not isinstance(op, dict):
            continue
        operation_type = op.get("type")
        if operation_type not in _VALID_OP_TYPES:
            continue
        target = str(op.get("target", "")).strip()
        if not target:
            continue
        out.append(
            TidyOperation(
                type=operation_type,
                target=target,
                reason=str(op.get("reason", "")),
                canonical=str(op.get("canonical", "")),
                by=str(op.get("by", "")),
                new_fields=(
                    dict(op.get("new_fields", {})) if operation_type == "revise" else {}
                ),
            )
        )
    return tuple(out)
