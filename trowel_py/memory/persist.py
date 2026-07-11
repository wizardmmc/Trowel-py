"""persist a distilled draft into the memory store (slice-040 T9).

The Python landing gate: take a validated Draft, write its notes via
``write_note`` and its diary via ``write_diary``. It NEVER writes layer one
(C-4) — this module does not import ``seeds`` and never touches ``core.md``.

confidence is DERIVED from verification (C-2): ``inferred-untested`` → draft
(never stable); ``event-data-supported`` → evolving; ``verified`` → evolving.
``stable`` is reserved for the 041 human-review promotion path, so an untested
claim can never land as stable knowledge.
"""
from __future__ import annotations

from dataclasses import dataclass

from trowel_py.memory.draft import Draft
from trowel_py.memory.store import MemoryStore

#: verification → confidence. inferred-untested is forced to draft (C-2).
_VERIFICATION_TO_CONFIDENCE = {
    "inferred-untested": "draft",
    "event-data-supported": "evolving",
    "verified": "evolving",
}


@dataclass(frozen=True)
class PersistReport:
    """Outcome of landing one draft.

    Attributes:
        notes_written: number of notes written to notes/.
        diary_written: number of diary entries written to diary/day/.
        verification_counts: how many notes landed at each verification tier.
    """

    notes_written: int
    diary_written: int
    verification_counts: dict[str, int]


def persist_draft(store: MemoryStore, draft: Draft, today: str) -> PersistReport:
    """Land a validated draft into notes/ + diary/. Never writes layer one.

    Args:
        store: the MemoryStore to write into.
        draft: a Draft that has already passed ``validate_draft``.
        today: ISO ``YYYY-MM-DD`` for the created/updated stamps.

    Returns:
        PersistReport with counts + per-tier verification tally.
    """
    ver_counts: dict[str, int] = {}
    for note in draft.notes:
        confidence = _VERIFICATION_TO_CONFIDENCE.get(note.verification, "draft")
        store.write_note(
            {
                "type": "note",
                "title": note.title,
                "summary": note.summary,
                "tags": list(note.tags),
                "confidence": confidence,
                "verification": note.verification,
                "pain": note.pain,
                "created": today,
                "updated": today,
                "refs": 0,
                "last_ref": "",
                "retired": False,
                "__body": note.body,
            }
        )
        ver_counts[note.verification] = ver_counts.get(note.verification, 0) + 1
    for d in draft.diary:
        store.write_diary(
            {
                "type": "diary",
                "date": d.date,
                "layer": "day",
                "period": d.date,
                # promoted_knowledge stays empty here — the 041 tidy/promote
                # path backfills it when lifting knowledge out of this diary.
                "promoted_knowledge": [],
                "__body": d.events,
            }
        )
    return PersistReport(
        notes_written=len(draft.notes),
        diary_written=len(draft.diary),
        verification_counts=ver_counts,
    )
