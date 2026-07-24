"""note 效果缓存回写。"""

from __future__ import annotations

from datetime import tzinfo
from pathlib import Path
from typing import Any

from trowel_py.memory.recompute.effects import compute_note_effects
from trowel_py.memory.store import MemoryStore


def recompute_counters(
    root: Path | str,
    *,
    local_tz: tzinfo | None = None,
    store_cls: Any = MemoryStore,
    compute_effects_fn=compute_note_effects,
) -> dict[str, Any]:
    """用重算效果覆盖 note 缓存，并返回更新汇总。"""
    root_path = Path(root)
    store = store_cls(root_path)
    effects = compute_effects_fn(root_path, local_tz=local_tz)

    # 缓存不是事实源；没有存活证据时也必须把旧的非零值归零。
    touched = set(effects)
    for stem, note in store.load_notes_with_id():
        if stem in touched:
            continue
        if (
            note.refs
            or note.read_sessions
            or note.helpful_refs
            or note.harmful_refs
            or note.last_ref
        ):
            touched.add(stem)

    updated = 0
    refs_total = 0
    read_sessions_total = 0
    helpful_total = 0
    harmful_total = 0
    for stem in touched:
        if store.load_note(stem) is None:
            continue
        eff = effects.get(stem)
        if eff is None:
            fields: dict[str, Any] = {
                "refs": 0,
                "read_sessions": 0,
                "helpful_refs": 0,
                "harmful_refs": 0,
                "last_ref": "",
            }
        else:
            fields = {
                "refs": eff.refs,
                "read_sessions": eff.read_session_count,
                "helpful_refs": eff.helpful_refs,
                "harmful_refs": eff.harmful_refs,
                "last_ref": eff.last_ref,
            }
            refs_total += eff.refs
            read_sessions_total += eff.read_session_count
            helpful_total += eff.helpful_refs
            harmful_total += eff.harmful_refs
        store.update_note_fields(stem, fields)
        updated += 1

    return {
        "updated": updated,
        "refs_total": refs_total,
        "read_sessions_total": read_sessions_total,
        "helpful_total": helpful_total,
        "harmful_total": harmful_total,
    }
