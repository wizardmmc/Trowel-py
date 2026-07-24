"""Draft 到 notes、episode、meta 与 completion manifest 的编排。"""

from __future__ import annotations

import json
from datetime import datetime

from trowel_py.memory.draft import Draft
from trowel_py.memory.store import MemoryStore
from trowel_py.memory.types import PersistContext

from .artifacts import _write_meta
from .manifest import (
    _SEGMENTS_META_DIR,
    _manifest_intact,
    _report_from_manifest,
)
from .models import PersistReport
from .notes import _content_hash, _update_note, _write_new_note

_REFLECTIONS_DIR = "meta/reflections"
_ESCALATIONS_DIR = "meta/escalations"


def persist_draft(
    store: MemoryStore,
    draft: Draft,
    context: PersistContext,
) -> PersistReport:
    """按 note、episode、meta、manifest 的顺序落盘。"""
    root = store.root
    segment_meta = root / _SEGMENTS_META_DIR
    manifest_path = segment_meta / f"{context.segment_id}.json"
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if _manifest_intact(root, manifest):
            return _report_from_manifest(manifest)

    today = context.review_date
    created: list[str] = []
    updated: list[str] = []
    verification_counts: dict[str, int] = {}
    for note in draft.notes:
        content_hash = _content_hash(note)
        verification_counts[note.verification] = (
            verification_counts.get(note.verification, 0) + 1
        )
        existing = store.find_note_by_source(
            context.cc_session_id,
            content_hash,
        )
        if existing is not None:
            _update_note(
                store,
                existing,
                note,
                content_hash,
                context,
                today,
            )
            updated.append(existing)
        else:
            created.append(
                _write_new_note(
                    store,
                    note,
                    content_hash,
                    context,
                    today,
                )
            )

    store.write_episode(context, draft.diary)
    reflection_written = _write_meta(
        store,
        _REFLECTIONS_DIR,
        "reflection",
        context,
        draft.reflection,
    )
    escalation_items = [item for item in draft.escalate_to_human if item.strip()]
    escalation_written = _write_meta(
        store,
        _ESCALATIONS_DIR,
        "escalation",
        context,
        "\n".join(f"- {item}" for item in escalation_items),
    )

    manifest = {
        "segment_id": context.segment_id,
        "cc_session_id": context.cc_session_id,
        "review_date": context.review_date,
        "completed_at": datetime.now().isoformat(timespec="seconds"),
        "notes_created": tuple(created),
        "notes_updated": tuple(updated),
        "episode_file": f"episodes/{context.cc_session_id}.md",
        "reflection_file": (
            f"{_REFLECTIONS_DIR}/{context.cc_session_id}.md"
            if reflection_written
            else None
        ),
        "escalation_file": (
            f"{_ESCALATIONS_DIR}/{context.cc_session_id}.md"
            if escalation_written
            else None
        ),
    }
    segment_meta.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return PersistReport(
        notes_written=len(created) + len(updated),
        diary_written=1,
        verification_counts=verification_counts,
        notes_created=tuple(created),
        notes_updated=tuple(updated),
        notes_skipped=(),
        episode_written=True,
        reflection_written=reflection_written,
        escalation_written=escalation_written,
        manifest_path=f"{_SEGMENTS_META_DIR}/{context.segment_id}.json",
        ok=True,
    )
