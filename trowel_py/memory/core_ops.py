"""管理 Core 条目的人工提名、审批和激活流程。

提名只写入候选文件；审批和激活会重写 ``core.md``。
"""

from __future__ import annotations

import re
from pathlib import Path

from trowel_py.memory.store import MemoryStore, _split_frontmatter
from trowel_py.memory.types import CoreItem

_SAFE_MEMORY_ID = re.compile(r"^[A-Za-z0-9_-]+$")


def nominate_candidate(root: Path | str, note_stem: str) -> str:
    """把现有 Note 写入人工晋升候选目录，不修改 ``core.md``。

    同一 ``memory_id`` 已有候选文件时会直接覆盖，包括原来标为 ``blocked`` 的候选。

    Args:
        root: 包含 Note 和 Core 数据的 Memory 根目录。
        note_stem: Note 文件名中不含 ``.md`` 的部分，不是 ``memory_id``。

    Returns:
        候选 Note 的 ``memory_id``。

    Raises:
        FileNotFoundError: 指定 Note 不存在或无法解析。
        ValueError: Note 没有 ``memory_id``，或其 ``memory_id`` 不能安全用于文件名。
    """
    from trowel_py.memory.tidy import _write_candidate

    store = MemoryStore(root)
    note = store.load_note(note_stem)
    if note is None:
        raise FileNotFoundError(f"no note with stem {note_stem!r}")
    if not note.memory_id:
        raise ValueError(f"note {note_stem!r} has no memory_id (run migrate first)")
    if not _SAFE_MEMORY_ID.match(note.memory_id):
        raise ValueError(
            f"note {note_stem!r} has unsafe memory_id {note.memory_id!r} (C-8)"
        )
    _write_candidate(Path(root), note)
    return note.memory_id


def approve_candidate(root: Path | str, candidate_id: str) -> str:
    """审批候选，并以 ``trial`` 状态将其追加到 ``core.md``。

    若源 Note 仍存在，使用其标题和摘要生成 Core 行为要求；否则使用候选文件的
    ``source_title``，该字段也不存在时使用 ``candidate_id``。处于 ``blocked``
    状态的候选、非 ``active`` 的源 Note 和 ``core.md`` 中已有的条目都不会获批。
    新条目固定使用 ``scope="high-risk"`` 和 ``source="monthly-promote"``。审批会
    重写 ``core.md``，但不会修改或删除候选文件。

    Args:
        root: 包含候选文件、Note 和 ``core.md`` 的 Memory 根目录。
        candidate_id: 候选文件名去掉 ``.md`` 后的部分；候选由 Note 生成时等于
            ``Note.memory_id``。

    Returns:
        新增 Core 条目的 ID，即传入的 ``candidate_id``。

    Raises:
        FileNotFoundError: 找不到指定候选文件。
        ValueError: 候选 ID 不安全、候选已被阻断、Core 条目已存在，或源 Note
            不是 ``active`` 状态。
    """
    if not candidate_id or not _SAFE_MEMORY_ID.match(candidate_id):
        raise ValueError(f"unsafe candidate_id {candidate_id!r}")
    cand_path = Path(root) / "meta" / "core-candidates" / f"{candidate_id}.md"
    if not cand_path.exists():
        raise FileNotFoundError(f"no candidate {candidate_id!r}")
    fm, _body = _split_frontmatter(cand_path.read_text(encoding="utf-8"))
    if fm and fm.get("status") == "blocked":
        raise ValueError(
            f"candidate {candidate_id!r} is blocked (harmful evidence); not approving"
        )
    store = MemoryStore(root)
    items = list(store.load_core_items())
    if any(it.id == candidate_id for it in items):
        raise ValueError(f"core.md already has item {candidate_id!r}")
    note = _find_note_by_memory_id(store, candidate_id)
    if note is not None and note.status != "active":
        raise ValueError(
            f"note {candidate_id!r} is {note.status!r}, not active; not approving"
        )
    if note is not None:
        imperative = f"{note.title}：{note.summary}"
    else:
        fm, _body = _split_frontmatter(cand_path.read_text(encoding="utf-8"))
        imperative = str((fm or {}).get("source_title", candidate_id))
    items.append(
        CoreItem(
            id=candidate_id,
            imperative=imperative,
            scope="high-risk",
            status="trial",
            source="monthly-promote",
        )
    )
    _rewrite_core(root, tuple(items))
    return candidate_id


def activate_core_item(root: Path | str, memory_id: str) -> str:
    """将指定 Core 条目改为 ``active``，并保留其他字段和条目顺序。

    Args:
        root: 包含 ``core.md`` 的 Memory 根目录。
        memory_id: 要激活的 Core 条目 ID。

    Returns:
        已激活的 Core 条目 ID，即传入的 ``memory_id``。

    Raises:
        FileNotFoundError: ``core.md`` 中不存在指定条目。
    """
    store = MemoryStore(root)
    items = list(store.load_core_items())
    found = False
    new_items: list[CoreItem] = []
    for it in items:
        if it.id == memory_id:
            found = True
            new_items.append(
                CoreItem(
                    id=it.id,
                    imperative=it.imperative,
                    scope=it.scope,
                    status="active",
                    source=it.source,
                )
            )
        else:
            new_items.append(it)
    if not found:
        raise FileNotFoundError(f"no core item {memory_id!r}")
    _rewrite_core(root, tuple(new_items))
    return memory_id


def _find_note_by_memory_id(store: MemoryStore, memory_id: str):
    """在可解析的 Note 文件中查找具有指定 ``memory_id`` 的记录。

    Args:
        store: 提供 Note 数据的 Memory 存储。
        memory_id: 要匹配的稳定 Note 标识。

    Returns:
        首条匹配的 Note；没有匹配项时返回 ``None``。
    """
    for _stem, n in store.load_notes_with_id():
        if n.memory_id == memory_id:
            return n
    return None


def _rewrite_core(root: Path | str, items: tuple[CoreItem, ...]) -> None:
    """用给定条目重新生成完整的 ``core.md``。

    Args:
        root: ``core.md`` 所在的 Memory 根目录。
        items: 按写入顺序排列的全部 Core 条目。
    """
    from trowel_py.memory.seeds import _render_core_md

    path = Path(root) / "core.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_render_core_md(items), encoding="utf-8")
