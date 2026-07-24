"""Core 候选的人工提名、审批与激活流程；只有显式操作会改写 ``core.md``。"""

from __future__ import annotations

import re
from pathlib import Path

from trowel_py.memory.store import MemoryStore, _split_frontmatter
from trowel_py.memory.types import CoreItem

_SAFE_MEMORY_ID = re.compile(r"^[A-Za-z0-9_-]+$")


def nominate_candidate(root: Path | str, note_stem: str) -> str:
    """把现有 note 手动写为候选并返回其 ``memory_id``。"""
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
    """审批候选并以 trial 状态写入 ``core.md``；blocked 或失活来源会被拒绝。"""
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
    """将指定 core item 重写为 active；目标不存在时失败。"""
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
    for _stem, n in store.load_notes_with_id():
        if n.memory_id == memory_id:
            return n
    return None


def _rewrite_core(root: Path | str, items: tuple[CoreItem, ...]) -> None:
    from trowel_py.memory.seeds import _render_core_md

    path = Path(root) / "core.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_render_core_md(items), encoding="utf-8")
