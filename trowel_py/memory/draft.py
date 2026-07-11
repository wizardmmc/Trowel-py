"""draft schema + gate for the write loop (slice-040 T7).

The distillation agent emits ``draft.json``; Python parses + validates it
BEFORE any ``write_note`` / ``write_diary`` (the schema gate, C-2/C-3).
``validate_draft`` rejects:
- notes missing a title,
- notes with an unknown verification tier,
- diary entries missing a date.

The "inferred-untested must not be stable" hard rule (C-2) is enforced one
layer down in ``persist`` (confidence is derived from verification there) —
the draft itself carries no confidence field, by design.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from trowel_py.memory.prompt import VERIFICATION_TIERS


@dataclass(frozen=True)
class DraftNote:
    """One knowledge candidate from the distillation agent."""

    title: str
    summary: str = ""
    body: str = ""
    tags: tuple[str, ...] = ()
    verification: str = "inferred-untested"
    verification_reason: str = ""
    pain: int = 0
    pain_reason: str = ""
    conflicts_with: tuple[str, ...] = ()


@dataclass(frozen=True)
class DraftDiary:
    """One experience-track event entry from the distillation agent."""

    date: str
    events: str = ""


@dataclass(frozen=True)
class Draft:
    """The full distilled draft (notes + diary + reflection + escalations)."""

    notes: tuple[DraftNote, ...] = ()
    diary: tuple[DraftDiary, ...] = ()
    reflection: str = ""
    escalate_to_human: tuple[str, ...] = ()


def parse_draft(text: str) -> Draft:
    """Parse agent draft.json text into a Draft.

    Raises:
        json.JSONDecodeError: the text is not valid JSON.
    """
    data = json.loads(text)
    notes = tuple(_parse_note(n) for n in (data.get("notes") or []))
    diary = tuple(_parse_diary(d) for d in (data.get("diary") or []))
    return Draft(
        notes=notes,
        diary=diary,
        reflection=str(data.get("reflection") or ""),
        escalate_to_human=tuple(data.get("escalate_to_human") or ()),
    )


def validate_draft(draft: Draft) -> list[str]:
    """Return validation errors (empty list = valid). C-2/C-3 gate.

    The persist layer calls this before any write_note/write_diary. A non-empty
    list means the whole draft is rejected (no partial write).
    """
    errors: list[str] = []
    for i, n in enumerate(draft.notes):
        if not n.title.strip():
            errors.append(f"notes[{i}]: missing title")
        if n.verification not in VERIFICATION_TIERS:
            errors.append(
                f"notes[{i}] {n.title!r}: unknown verification {n.verification!r}"
            )
    for i, d in enumerate(draft.diary):
        if not d.date.strip():
            errors.append(f"diary[{i}]: missing date")
    return errors


def _parse_note(n: dict[str, Any]) -> DraftNote:
    return DraftNote(
        title=str(n.get("title", "")),
        summary=str(n.get("summary", "")),
        body=str(n.get("body", "")),
        tags=tuple(n.get("tags") or ()),
        verification=str(n.get("verification", "inferred-untested")),
        verification_reason=str(n.get("verification_reason", "")),
        pain=int(n.get("pain") or 0),
        pain_reason=str(n.get("pain_reason", "")),
        conflicts_with=tuple(n.get("conflicts_with") or ()),
    )


def _parse_diary(d: dict[str, Any]) -> DraftDiary:
    return DraftDiary(date=str(d.get("date", "")), events=str(d.get("events", "")))
