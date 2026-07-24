"""Tidy 计划的纯校验边界。"""

from __future__ import annotations

from pathlib import Path

from trowel_py.memory.store import MemoryStore

from .models import TidyOperation, TidyPlan

_REVISE_ALLOWED_FIELDS = frozenset(
    {
        "summary",
        "verification",
        "verification_reason",
        "pain",
        "pain_reason",
        "trigger",
        "do_not_use_when",
        "valid_from",
        "last_verified_at",
        "tags",
        "sources",
        "conflicts_with",
    }
)


def _validate_revise_op(
    root: Path, op: TidyOperation, id_map: dict[str, str]
) -> list[str]:
    """只允许 revise 修改内容属性，并复核修改后的 schema。"""
    errs: list[str] = []
    bad = sorted(set(op.new_fields) - _REVISE_ALLOWED_FIELDS)
    if bad:
        errs.append(
            f"op revise: field(s) {bad} not in allowlist; revise may only set "
            f"{sorted(_REVISE_ALLOWED_FIELDS)} (C-2)"
        )
        return errs
    stem = id_map.get(op.target)
    if not stem:
        return errs
    from trowel_py.memory.schema import validate_entry
    from trowel_py.memory.store import _split_frontmatter

    path = root / "notes" / f"{stem}.md"
    fm, _body = _split_frontmatter(path.read_text(encoding="utf-8"))
    simulated = dict(fm or {})
    simulated.update(op.new_fields)
    vr = validate_entry("note", simulated)
    if not vr.ok:
        errs.append(f"op revise {op.target}: schema reject: {vr.errors}")
    return errs


def _memory_id_to_stem(root: Path) -> dict[str, str]:
    """建立稳定 ID 到文件名的映射，跳过尚未迁移的旧笔记。"""
    store = MemoryStore(root)
    return {
        note.memory_id: stem
        for stem, note in store.load_notes_with_id()
        if note.memory_id
    }


def validate_plan(root: Path, plan: TidyPlan) -> list[str]:
    """返回目标、字段和完整订正链上的全部校验错误。"""
    errors: list[str] = []
    id_map = _memory_id_to_stem(root)
    for op in plan.operations:
        if op.target not in id_map:
            errors.append(f"op {op.type}: target {op.target!r} not found in notes")
        if op.type == "merge_sources" and op.canonical not in id_map:
            errors.append(f"op merge_sources: canonical {op.canonical!r} not found")
        if op.type in ("supersede", "contradict") and op.by not in id_map:
            errors.append(f"op {op.type}: by {op.by!r} not found")
        replacer = op.by or op.canonical
        if replacer and op.target == replacer:
            errors.append(
                f"op {op.type}: target {op.target!r} cannot replace itself (自指)"
            )
        if op.type == "revise":
            errors.extend(_validate_revise_op(root, op, id_map))

    store = MemoryStore(root)
    edges: dict[str, str] = {}
    for _stem, note in store.load_notes_with_id():
        if note.memory_id and note.superseded_by:
            edges[note.memory_id] = note.superseded_by
    for op in plan.operations:
        if op.type in ("supersede", "contradict", "merge_sources"):
            replacer = op.by or op.canonical
            if replacer:
                edges[op.target] = replacer
    if _has_cycle(edges):
        errors.append(
            "supersede/contradict/merge chain has a cycle (订正链成环，含已有订正链)"
        )
    return errors


def _has_cycle(edges: dict[str, str]) -> bool:
    """判断 target 到 replacer 的有向图是否成环。"""
    WHITE, GRAY, BLACK = 0, 1, 2
    color: dict[str, int] = {node: WHITE for node in set(edges) | set(edges.values())}

    def dfs(node: str) -> bool:
        color[node] = GRAY
        nxt = edges.get(node)
        if nxt is not None:
            next_color = color.get(nxt, WHITE)
            if next_color == GRAY:
                return True
            if next_color == WHITE and dfs(nxt):
                return True
        color[node] = BLACK
        return False

    return any(color[node] == WHITE and dfs(node) for node in list(color))
