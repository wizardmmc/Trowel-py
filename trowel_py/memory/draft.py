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
    kind: str = "fact"
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


#: slice-040-a C-3 soft gate: the four elements a kind=procedure body should
#: carry, each with CN/EN aliases. Matched case-insensitively against the body
#: so the gate judges direction, not exact wording (D5: warn, don't reject).
_PROCEDURE_ELEMENTS: dict[str, tuple[str, ...]] = {
    "trigger": ("trigger", "触发", "场景是", "什么场景"),
    "procedure": ("procedure", "做法", "步骤", "怎么做"),
    "stop": ("stop", "何时停", "停止条件", "终止"),
    "anti-pattern": ("anti-pattern", "anti pattern", "别做", "不要", "反面"),
}


def procedure_warnings(draft: Draft) -> list[str]:
    """Soft-check procedural notes carry the four elements (slice-040-a C-3).

    A ``kind=procedure`` note should describe trigger / procedure / stop /
    anti-pattern so the user's standing ask ("don't make me remind you that
    we've hit this before") is answered by an actionable procedure, not just a
    declarative fact. Missing elements return a warning string; the gate never
    rejects (D5 — help the model, don't constrain it).

    Returns:
        warning strings (empty list when every procedure note is complete or
        no note is procedural).
    """
    warnings: list[str] = []
    for i, n in enumerate(draft.notes):
        if n.kind != "procedure":
            continue
        if not n.body.strip():
            warnings.append(f"notes[{i}] {n.title!r}: kind=procedure but body empty")
            continue
        body_lower = n.body.lower()
        for elem, aliases in _PROCEDURE_ELEMENTS.items():
            if not any(a.lower() in body_lower for a in aliases):
                warnings.append(
                    f"notes[{i}] {n.title!r}: kind=procedure but body may miss "
                    f"'{elem}'"
                )
    return warnings


def _parse_note(n: dict[str, Any]) -> DraftNote:
    return DraftNote(
        title=str(n.get("title", "")),
        summary=str(n.get("summary", "")),
        body=str(n.get("body", "")),
        tags=tuple(n.get("tags") or ()),
        kind=str(n.get("kind", "fact")),
        verification=str(n.get("verification", "inferred-untested")),
        verification_reason=str(n.get("verification_reason", "")),
        pain=int(n.get("pain") or 0),
        pain_reason=str(n.get("pain_reason", "")),
        conflicts_with=tuple(n.get("conflicts_with") or ()),
    )


def _parse_diary(d: dict[str, Any]) -> DraftDiary:
    return DraftDiary(date=str(d.get("date", "")), events=str(d.get("events", "")))
