"""Reflection 与 escalation 产物。"""

from trowel_py.memory.store import MemoryStore, _dump_frontmatter
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
    frontmatter = {
        "type": meta_type,
        "cc_session_id": context.cc_session_id,
        "segment_id": context.segment_id,
        "review_date": context.review_date,
    }
    path.write_text(
        _dump_frontmatter(frontmatter, body.strip() + "\n"),
        encoding="utf-8",
    )
    return True
