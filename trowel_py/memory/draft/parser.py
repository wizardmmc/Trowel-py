"""Draft JSON 的宽松兼容解析。"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any


def parse_draft(
    text: str,
    *,
    loads: Callable[[str], Any],
    draft_type: Callable[..., Any],
    parse_note: Callable[[dict[str, Any]], Any],
    parse_diary: Callable[[dict[str, Any]], Any],
) -> Any:
    data = loads(text)
    notes = tuple(parse_note(note) for note in (data.get("notes") or []))
    diary = tuple(parse_diary(item) for item in (data.get("diary") or []))
    return draft_type(
        notes=notes,
        diary=diary,
        reflection=str(data.get("reflection") or ""),
        escalate_to_human=tuple(data.get("escalate_to_human") or ()),
    )


def parse_note(
    note: dict[str, Any],
    *,
    note_type: Callable[..., Any],
) -> Any:
    return note_type(
        title=str(note.get("title", "")),
        summary=str(note.get("summary", "")),
        body=str(note.get("body", "")),
        tags=tuple(note.get("tags") or ()),
        kind=str(note.get("kind", "fact")),
        verification=str(note.get("verification", "inferred-untested")),
        verification_reason=str(note.get("verification_reason", "")),
        pain=int(note.get("pain") or 0),
        pain_reason=str(note.get("pain_reason", "")),
        conflicts_with=tuple(note.get("conflicts_with") or ()),
    )


def parse_diary(
    diary: dict[str, Any],
    *,
    diary_type: Callable[..., Any],
    str_list: Callable[[Any], tuple[str, ...]],
) -> Any:
    return diary_type(
        date=str(diary.get("date", "")),
        outcomes=str_list(diary.get("outcomes")),
        decisions=str_list(diary.get("decisions")),
        corrections=str_list(diary.get("corrections")),
        open_loops=str_list(diary.get("open_loops")),
        events=str(diary.get("events") or ""),
    )


def str_list(value: Any) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    return tuple(item for item in (str(raw).strip() for raw in value) if item)
