"""Reflection 与 escalation 产物。"""

from typing import Any

from trowel_py.memory.store import MemoryStore, _dump_frontmatter
from trowel_py.memory.provenance import completed_segment_to_dict, derivation_to_dict
from trowel_py.memory.types import PersistContext


def _write_meta(
    store: MemoryStore,
    rel_dir: str,
    meta_type: str,
    context: PersistContext,
    body: str,
) -> bool:
    """空正文不落盘；同一 session 的产物覆盖更新。"""
    if not body.strip():
        return False
    path = store.root / rel_dir / f"{context.cc_session_id}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    frontmatter: dict[str, Any] = {
        "type": meta_type,
        "cc_session_id": context.cc_session_id,
        "segment_id": context.segment_id,
        "review_date": context.review_date,
    }
    if context.completed_segment is not None:
        frontmatter["source"] = completed_segment_to_dict(context.completed_segment)
    if context.derivation is not None:
        frontmatter["derivation"] = derivation_to_dict(context.derivation)
    path.write_text(
        _dump_frontmatter(frontmatter, body.strip() + "\n"),
        encoding="utf-8",
    )
    return True
