"""用重新聚合的证据覆盖 note 效果缓存。"""

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
    """重算并覆盖 note 的效果缓存字段。

    只改写有重算效果或仍带旧非零缓存的 note。后者会被归零，使缓存始终可以
    从当前证据重建；重算效果指向但存储中已不存在的 note 会被跳过。

    Args:
        root: memory 根目录。
        local_tz: 聚合读取日期采用的时区。
        store_cls: 用于读写 note 的存储实现。
        compute_effects_fn: 按 note stem 返回重算效果的函数。

    Returns:
        ``updated`` 为实际改写的 note 数，其余字段为有效重算效果的读取事件、
        读取会话、有帮助会话和有害会话合计。清除旧缓存不增加这些合计。
    """
    root_path = Path(root)
    store = store_cls(root_path)
    effects = compute_effects_fn(root_path, local_tz=local_tz)

    # 缓存必须能从当前证据重建，因此已失去证据的旧非零值也要归零。
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
