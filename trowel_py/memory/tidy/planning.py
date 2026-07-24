"""由 LLM 输出受限 TidyPlan，不直接修改笔记。"""

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
    """判断 ISO 日期是否位于目标周。"""
    from trowel_py.memory.compress import _in_iso_week

    return _in_iso_week(date_str, iso_year, iso_week)


def build_tidy_plan(
    root: Path | str,
    iso_week: str,
    provider: LLMProvider,
    *,
    plan_id: str | None = None,
) -> TidyPlan:
    """根据本周笔记、L1 索引和冲突笔记生成计划。"""
    from trowel_py.memory.compress import _parse_iso_week

    iso_year, iso_week_num = _parse_iso_week(iso_week)

    def in_scope(date_str: str) -> bool:
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
    """根据目标月份内新建或更新的笔记生成计划。"""

    def in_scope(date_str: str) -> bool:
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
    """收集范围内上下文，并将模型结果过滤为已知 memory_id。"""
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
    """解析模型 JSON，忽略未知类型、空目标和非 JSON 输出。"""
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
                type=operation_type,  # type: ignore[arg-type]
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
