"""Draft 的硬校验与 procedure 软告警。"""

from __future__ import annotations

from collections.abc import Collection, Mapping, Sequence
from typing import Any

from trowel_py.memory.draft.episode import (
    DraftCorrection,
    DraftDecision,
    DraftOpenLoop,
)


def validate_draft(
    draft: Any,
    *,
    note_kinds: Collection[str],
    verification_tiers: Collection[str],
    legal_source_refs: set[str] | None,
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
                "draft; use items"
            )
        legacy_fields = tuple(
            field_name
            for field_name in (
                "outcomes",
                "decisions",
                "corrections",
                "open_loops",
            )
            if getattr(diary, field_name) and not diary.items
        )
        if legacy_fields:
            errors.append(
                f"diary[{index}]: legacy structured lists are not allowed in a new "
                "draft; use items"
            )
        for item_index, item in enumerate(diary.items):
            prefix = f"diary[{index}].items[{item_index}]"
            if isinstance(item, DraftCorrection):
                if not item.before:
                    errors.append(f"{prefix}.before must not be empty")
                if not item.after:
                    errors.append(f"{prefix}.after must not be empty")
                if not item.reason:
                    errors.append(f"{prefix}.reason must not be empty")
            else:
                if not item.summary:
                    errors.append(f"{prefix}.summary must not be empty")
            if isinstance(item, DraftDecision):
                if not item.reason:
                    errors.append(f"{prefix}.reason must not be empty")
                if item.status not in {"active", "superseded"}:
                    errors.append(
                        f"{prefix}.status must be one of ['active', 'superseded']"
                    )
            if isinstance(item, DraftOpenLoop):
                if not item.reason:
                    errors.append(f"{prefix}.reason must not be empty")
                if item.status not in {"active", "closed"}:
                    errors.append(
                        f"{prefix}.status must be one of ['active', 'closed']"
                    )
            refs = item.source_refs
            if not refs:
                errors.append(f"{prefix}.source_refs must not be empty")
            elif any(not ref for ref in refs):
                errors.append(f"{prefix}.source_refs contains empty refs")
            elif len(refs) != len(set(refs)):
                errors.append(f"{prefix}.source_refs contains duplicates")
            if legal_source_refs is not None:
                illegal = [ref for ref in refs if ref not in legal_source_refs]
                if illegal:
                    errors.append(
                        f"{prefix}.source_refs contains illegal refs: {illegal!r}"
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
