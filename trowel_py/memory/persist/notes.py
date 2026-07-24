"""Persist 的 note 身份、创建与重判更新。"""

from __future__ import annotations

import hashlib

from trowel_py.memory.draft import DraftNote
from trowel_py.memory.ids import uuid7
from trowel_py.memory.store import MemoryStore, _split_frontmatter
from trowel_py.memory.types import PersistContext


def _content_hash(note: DraftNote) -> str:
    """身份哈希排除 verification 与 pain 等可变判断。"""
    raw = "\n".join([note.title, note.summary, note.body, note.kind])
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def _write_new_note(
    store: MemoryStore,
    note: DraftNote,
    content_hash: str,
    context: PersistContext,
    today: str,
) -> str:
    return store.write_note(
        {
            "type": "note",
            "title": note.title,
            "kind": note.kind,
            "summary": note.summary,
            "tags": list(note.tags),
            "verification": note.verification,
            "verification_reason": note.verification_reason,
            "pain": note.pain,
            "pain_reason": note.pain_reason,
            "conflicts_with": list(note.conflicts_with),
            "memory_id": str(uuid7()),
            "status": "active",
            "valid_from": today,
            "created": today,
            "updated": today,
            "refs": 0,
            "helpful_refs": 0,
            "harmful_refs": 0,
            "last_ref": "",
            "sources": [context.cc_session_id],
            "source_sessions": [context.cc_session_id],
            "content_hash": content_hash,
            "__body": note.body,
        }
    )


def _update_note(
    store: MemoryStore,
    note_id: str,
    note: DraftNote,
    content_hash: str,
    context: PersistContext,
    today: str,
) -> None:
    """重判只更新可变字段，并合并两套来源字段。"""
    path = store.root / "notes" / f"{note_id}.md"
    frontmatter, _body = _split_frontmatter(path.read_text(encoding="utf-8"))
    sessions = set(frontmatter.get("source_sessions") or []) if frontmatter else set()
    sessions.add(context.cc_session_id)
    provenance = set(frontmatter.get("sources") or []) if frontmatter else set()
    provenance.add(context.cc_session_id)
    store.update_note_fields(
        note_id,
        {
            "verification": note.verification,
            "verification_reason": note.verification_reason,
            "pain": note.pain,
            "pain_reason": note.pain_reason,
            "conflicts_with": list(note.conflicts_with),
            "updated": today,
            "source_sessions": sorted(sessions),
            "sources": sorted(provenance),
            "content_hash": content_hash,
        },
    )
