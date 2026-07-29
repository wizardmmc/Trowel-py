"""只读校验 Tidy 计划的目标、修订字段和订正链。"""

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
    """校验 ``revise`` 字段白名单和修改后的 Note schema。

    非白名单字段会立即返回错误，不再读取目标或模拟 schema；字段白名单通过
    但目标不存在时返回空列表，由调用方报告目标缺失。未被 frontmatter 解析层
    处理的读取或字段转换异常直接传播。

    Args:
        root: 记忆目录。
        op: 要校验的 ``revise`` 操作。
        id_map: Note 记忆 ID 到文件 stem 的映射。

    Returns:
        字段白名单或模拟 schema 错误。
    """
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
    """建立 Note 记忆 ID 到文件 stem 的映射。

    ``memory_id`` 为空的 Note 会被跳过；重复 ID 保留路径排序最后一个文件的
    stem。

    Args:
        root: 记忆目录。

    Returns:
        非空记忆 ID 到文件 stem 的映射。
    """
    store = MemoryStore(root)
    return {
        note.memory_id: stem
        for stem, note in store.load_notes_with_id()
        if note.memory_id
    }


def validate_plan(root: Path, plan: TidyPlan) -> list[str]:
    """校验计划引用、``revise`` 字段及合并后的订正链。

    订正图先读取现有 ``superseded_by``，再按计划顺序覆盖同一目标的出边。
    每个操作都优先取非空 ``by``，否则取 ``canonical``；因此畸形的
    ``merge_sources`` 同时携带两者时，校验图可能与执行时采用的 ``canonical``
    不同。本函数不检查各操作目标的 ``content_hash``；``apply_plan`` 会在执行
    前按 ``expected_revision`` 或 ``source_snapshot`` 复核。读取和字段转换中
    未被存储层处理的异常直接传播。

    Args:
        root: 记忆目录。
        plan: 要校验的整理计划。

    Returns:
        按操作检查顺序排列的错误；无错误时为空列表。
    """
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
    """判断每个节点至多一条出边的订正图是否成环。

    Args:
        edges: 被替代 Note 到替代 Note 的映射。

    Returns:
        任一连通路径形成有向环时为 ``True``。
    """
    WHITE, GRAY, BLACK = 0, 1, 2
    color: dict[str, int] = {node: WHITE for node in set(edges) | set(edges.values())}

    def dfs(node: str) -> bool:
        """深度遍历一个节点，并报告是否回到当前递归路径。"""
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
