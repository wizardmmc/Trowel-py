"""Draft 的硬校验与 procedure 软告警。"""

from __future__ import annotations

from collections.abc import Collection, Mapping, Sequence
from typing import Any


def validate_draft(
    draft: Any,
    *,
    note_kinds: Collection[str],
    verification_tiers: Collection[str],
    max_items_per_date: int,
    max_items_per_field: int,
    max_item_chars: int,
    max_total_chars: int,
) -> list[str]:
    errors: list[str] = []
    for index, note in enumerate(draft.notes):
        if not note.title.strip():
            errors.append(f"notes[{index}]: missing title")
        if note.kind not in note_kinds:
            errors.append(
                f"notes[{index}] {note.title!r}: unknown kind {note.kind!r}; "
                f"expected one of {list(note_kinds)!r}"
            )
        if note.verification not in verification_tiers:
            errors.append(
                f"notes[{index}] {note.title!r}: unknown verification "
                f"{note.verification!r}"
            )

    for index, diary in enumerate(draft.diary):
        if not diary.date.strip():
            errors.append(f"diary[{index}]: missing date")
        if diary.events.strip():
            errors.append(
                f"diary[{index}]: legacy events are not allowed in a new "
                "draft; use outcomes/decisions/corrections/open_loops"
            )
        structured = diary.all_items()
        if len(structured) > max_items_per_date:
            errors.append(
                f"diary[{index}]: too many structured items "
                f"({len(structured)} > {max_items_per_date})"
            )
        for field_name in (
            "outcomes",
            "decisions",
            "corrections",
            "open_loops",
        ):
            field_items = getattr(diary, field_name)
            if len(field_items) > max_items_per_field:
                errors.append(
                    f"diary[{index}].{field_name}: too many items "
                    f"({len(field_items)} > {max_items_per_field})"
                )
            for item_index, item in enumerate(field_items):
                if len(item) > max_item_chars:
                    errors.append(
                        f"diary[{index}].{field_name}[{item_index}]: item "
                        f"exceeds {max_item_chars} chars"
                    )
        total_chars = sum(len(item) for item in structured)
        if total_chars > max_total_chars:
            errors.append(
                f"diary[{index}]: structured text exceeds "
                f"{max_total_chars} chars ({total_chars})"
            )
    return errors


def procedure_warnings(
    draft: Any,
    *,
    elements: Mapping[str, Sequence[str]],
) -> list[str]:
    warnings: list[str] = []
    for index, note in enumerate(draft.notes):
        if note.kind != "procedure":
            continue
        if not note.body.strip():
            warnings.append(
                f"notes[{index}] {note.title!r}: kind=procedure but body empty"
            )
            continue
        body_lower = note.body.lower()
        for element, aliases in elements.items():
            if not any(alias.lower() in body_lower for alias in aliases):
                warnings.append(
                    f"notes[{index}] {note.title!r}: kind=procedure but body "
                    f"may miss '{element}'"
                )
    return warnings
