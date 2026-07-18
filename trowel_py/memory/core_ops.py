"""layer-one human-triggered promotion (slice-041 C-5/C-11).

The monthly tidy only WRITES candidate files (``meta/core-candidates/``); it
never touches ``core.md``. Promotion into layer one is a two-step human
action: ``nominate`` (a gotcha you judge important even below the helpful
threshold) → ``approve`` (candidate → core.md item, status=trial) →
``activate`` (trial → active after probation). All three are CLI-only;
no automatic path writes core.md.
"""
from __future__ import annotations

import re
from pathlib import Path

from trowel_py.memory.store import MemoryStore, _split_frontmatter
from trowel_py.memory.types import CoreItem

_SAFE_MEMORY_ID = re.compile(r"^[A-Za-z0-9_-]+$")


def nominate_candidate(root: Path | str, note_stem: str) -> str:
    """Promote a note to a core-candidate by hand (below the helpful threshold).

    Reads the note, writes ``meta/core-candidates/<memory_id>.md``. The note
    must already carry a ``memory_id`` (run ``trowel memory migrate`` first).
    Returns the memory_id.
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
    """Move a candidate into ``core.md`` as ``status=trial`` (human trigger).

    ``candidate_id`` is the memory_id (the candidate filename stem). Refuses if
    the id is path-unsafe, the candidate is missing, blocked (harmful evidence
    — slice-065 C-3), already in core.md, or its note is no longer active.
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
    items.append(CoreItem(
        id=candidate_id, imperative=imperative, scope="high-risk",
        status="trial", source="monthly-promote",
    ))
    _rewrite_core(root, tuple(items))
    return candidate_id


def activate_core_item(root: Path | str, memory_id: str) -> str:
    """Flip a core.md item from ``trial`` → ``active`` (human trigger)."""
    store = MemoryStore(root)
    items = list(store.load_core_items())
    found = False
    new_items: list[CoreItem] = []
    for it in items:
        if it.id == memory_id:
            found = True
            new_items.append(CoreItem(
                id=it.id, imperative=it.imperative, scope=it.scope,
                status="active", source=it.source,
            ))
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
    """Rewrite ``core.md`` from a full item list (human-triggered writes only)."""
    from trowel_py.memory.seeds import _render_core_md

    path = Path(root) / "core.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_render_core_md(items), encoding="utf-8")
