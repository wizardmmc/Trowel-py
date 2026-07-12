"""persist a distilled draft into the memory store (slice-040-a).

The Python landing gate. Takes a validated Draft + a PersistContext (the
session's provenance — persist MUST NOT guess its source), and lands:

- notes (knowledge track) via ``write_note`` — full fields, idempotent;
- episode (experience track) via ``write_episode`` — per-session, never
  overwrites a sibling session (P1 fix);
- reflection / escalation via per-session files under ``meta/``;
- a completion manifest written LAST — the C-7 atomic signal.

It NEVER writes layer one (C-4) — this module does not import ``seeds`` and
never touches ``core.md``.

slice-041: ``confidence`` was removed (C-9 — it was a derived mirror of
``verification``). New notes get ``status="active"`` + a fresh UUIDv7
``memory_id`` (D1) + ``valid_from=today``. Idempotence (040-a D4) unchanged:
``(cc_session_id, content_hash)`` where content_hash = sha256(title+summary+
body+kind) — verification/pain re-judgements UPDATE the existing note.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime

from trowel_py.memory.draft import Draft, DraftNote
from trowel_py.memory.ids import uuid7
from trowel_py.memory.store import MemoryStore
from trowel_py.memory.store import _dump_frontmatter, _split_frontmatter
from trowel_py.memory.types import PersistContext

_SEGMENTS_META_DIR = "meta/persisted-segments"
_REFLECTIONS_DIR = "meta/reflections"
_ESCALATIONS_DIR = "meta/escalations"


@dataclass(frozen=True)
class PersistReport:
    """Outcome of landing one draft.

    Attributes:
        notes_written: legacy count (created + updated) for log parity with 040.
        diary_written: 1 if the episode landed, else 0.
        verification_counts: per-tier tally of notes landed this call.
        notes_created / notes_updated / notes_skipped: note ids by fate.
            ``skipped`` is non-empty on a manifest-no-op re-run.
        episode_written / reflection_written / escalation_written: product flags.
        manifest_path: the completion manifest path (relative to root), or None.
        ok: True iff every product + the manifest landed (the C-7 signal the
            caller gates ``mark_extracted`` on).
    """

    notes_written: int
    diary_written: int
    verification_counts: dict[str, int]
    notes_created: tuple[str, ...] = ()
    notes_updated: tuple[str, ...] = ()
    notes_skipped: tuple[str, ...] = ()
    episode_written: bool = False
    reflection_written: bool = False
    escalation_written: bool = False
    manifest_path: str | None = None
    ok: bool = False


def _content_hash(note: DraftNote) -> str:
    """D4 idempotence key: sha256(title + summary + body + kind), 16 hex.

    Deliberately excludes verification/pain — those are mutable judgments that
    can be re-judged without changing the note's identity.
    """
    raw = "\n".join([note.title, note.summary, note.body, note.kind])
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def persist_draft(
    store: MemoryStore, draft: Draft, context: PersistContext
) -> PersistReport:
    """Land a validated draft into notes/ + episode + meta/ + manifest.

    Idempotent: a re-run with an intact completion manifest is a no-op. A
    re-run without one re-lands, deduping notes by (cc_session_id, hash).
    Any mid-landing failure bubbles up (the manifest is written LAST, so a
    failure leaves no manifest → the session stays retryable).

    Args:
        store: the MemoryStore to write into.
        draft: a Draft that has already passed ``validate_draft``.
        context: the session's provenance (segment_id, cc_session_id, …).

    Returns:
        PersistReport; ``ok`` is True when the manifest landed.
    """
    root = store.root
    seg_meta_dir = root / _SEGMENTS_META_DIR
    # CR H-3 TODO: segment_id carries ":" (e.g. "s1:0:4096") which is legal in a
    # macOS filename but breaks tar/rsync backups and is illegal on Windows. If
    # this project ever needs cross-platform manifest files, map the filename
    # via segment_id.replace(":", "_") here (keep segment_id itself unchanged in
    # the manifest body + episode markers).
    manifest_path = seg_meta_dir / f"{context.segment_id}.json"

    # 0. completion manifest already intact → no-op (don't rewrite products).
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if _manifest_intact(root, manifest):
            return _report_from_manifest(manifest)

    today = context.review_date

    # 1. notes (idempotent by cc_session_id + content_hash).
    created: list[str] = []
    updated: list[str] = []
    ver_counts: dict[str, int] = {}
    for note in draft.notes:
        h = _content_hash(note)
        ver_counts[note.verification] = ver_counts.get(note.verification, 0) + 1
        existing = store.find_note_by_source(context.cc_session_id, h)
        if existing is not None:
            _update_note(store, existing, note, h, context, today)
            updated.append(existing)
        else:
            created.append(_write_new_note(store, note, h, context, today))

    # 2. episode (per-session; never overwrites a sibling session — P1 fix).
    store.write_episode(context, draft.diary)

    # 3. reflection / escalation (per-session meta files; empty → skip).
    reflection_written = _write_meta(
        store, _REFLECTIONS_DIR, "reflection", context, draft.reflection
    )
    escalation_items = [i for i in draft.escalate_to_human if i.strip()]
    escalation_written = _write_meta(
        store, _ESCALATIONS_DIR, "escalation", context,
        "\n".join(f"- {item}" for item in escalation_items),
    )

    # 4. completion manifest — written LAST (the C-7 atomic signal).
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
    seg_meta_dir.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    return PersistReport(
        notes_written=len(created) + len(updated),
        diary_written=1,
        verification_counts=ver_counts,
        notes_created=tuple(created),
        notes_updated=tuple(updated),
        notes_skipped=(),
        episode_written=True,
        reflection_written=reflection_written,
        escalation_written=escalation_written,
        manifest_path=f"{_SEGMENTS_META_DIR}/{context.segment_id}.json",
        ok=True,
    )


def _write_new_note(
    store: MemoryStore,
    note: DraftNote,
    content_hash: str,
    context: PersistContext,
    today: str,
) -> str:
    """Persist a brand-new note with full provenance fields. Returns the note id."""
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
            # slice-041: stable identity + lifecycle (C-7/C-9). New notes are
            # `active` (grill 2026-07-11 — no candidate state).
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
    """Re-judge path: same source+content, mutable fields updated in place.

    Merges source_sessions (adds this session if missing — 041 may merge across
    sources). Does NOT touch title/summary/body/kind (their hash IS the identity).

    TODO(041): reads the note file twice (once here to merge source_sessions,
    once inside ``update_note_fields``'s read-modify-write). Safe under the
    single-writer review job, but collapsing to one read removes the theoretical
    staleness window and the double parse.
    """
    path = store.root / "notes" / f"{note_id}.md"
    fm, _body = _split_frontmatter(path.read_text(encoding="utf-8"))
    sessions = set(fm.get("source_sessions") or []) if fm else set()
    sessions.add(context.cc_session_id)
    # slice-041: keep `sources` (multi-source provenance) in sync with
    # source_sessions on the re-judge path — merge_sources (TidyPlan) absorbs
    # siblings into `sources`, so a re-judge from another session must add
    # itself here too, or `sources` drifts behind `source_sessions`.
    provenance = set(fm.get("sources") or []) if fm else set()
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


def _write_meta(
    store: MemoryStore, rel_dir: str, meta_type: str,
    context: PersistContext, body: str,
) -> bool:
    """Write a per-session meta file (reflection or escalation). Empty body → skip.

    Overwrite-upsert by cc_session_id (NOT date-append) so a re-run never
    duplicates. The frontmatter is self-describing; these are NOT validated by
    ``validate_entry`` (they live under ``meta/``, away from notes/diary).

    Args:
        rel_dir: the meta subdirectory (``meta/reflections`` or ``meta/escalations``).
        meta_type: the frontmatter ``type`` value (``reflection`` / ``escalation``),
            passed explicitly so adding a third meta kind never silently mislabels.
    """
    if not body.strip():
        return False
    path = store.root / rel_dir / f"{context.cc_session_id}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    fm = {
        "type": meta_type,
        "cc_session_id": context.cc_session_id,
        "segment_id": context.segment_id,
        "review_date": context.review_date,
    }
    path.write_text(_dump_frontmatter(fm, body.strip() + "\n"), encoding="utf-8")
    return True


def _manifest_intact(root: Path, manifest: dict) -> bool:
    """True iff every product the manifest claims is still on disk.

    A manifest alone is NOT enough to skip (spec 接口契约): a stray delete must
    send us back through the landing path. Checks episode + every claimed note id
    + reflection/escalation when present.
    """
    ep = manifest.get("episode_file")
    if ep and not (root / ep).exists():
        return False
    for nid in list(manifest.get("notes_created", [])) + list(
        manifest.get("notes_updated", [])
    ):
        if not (root / "notes" / f"{nid}.md").exists():
            return False
    rfl = manifest.get("reflection_file")
    if rfl and not (root / rfl).exists():
        return False
    esc = manifest.get("escalation_file")
    if esc and not (root / esc).exists():
        return False
    return True


def _report_from_manifest(manifest: dict) -> PersistReport:
    """Reconstruct a no-op report from an intact manifest."""
    skipped = tuple(manifest.get("notes_created", [])) + tuple(
        manifest.get("notes_updated", [])
    )
    return PersistReport(
        notes_written=0,
        diary_written=0,
        verification_counts={},
        notes_created=(),
        notes_updated=(),
        notes_skipped=skipped,
        episode_written=True,
        reflection_written=manifest.get("reflection_file") is not None,
        escalation_written=manifest.get("escalation_file") is not None,
        manifest_path=f"{_SEGMENTS_META_DIR}/{manifest['segment_id']}.json",
        ok=True,
    )
