from __future__ import annotations

import inspect
from dataclasses import FrozenInstanceError, fields
from types import SimpleNamespace
from typing import Any, Callable, cast

import pytest

import trowel_py.memory.draft as draft_module
from trowel_py.memory.draft import (
    Draft,
    DraftDiary,
    DraftNote,
    _parse_diary,
    _parse_note,
    _str_list,
    parse_draft,
    procedure_warnings,
    validate_draft,
)


def test_facade_keeps_model_and_function_contracts() -> None:
    expected_signatures: dict[Callable[..., Any], str] = {
        DraftNote: (
            "(title: 'str', summary: 'str' = '', body: 'str' = '', "
            "tags: 'tuple[str, ...]' = (), kind: 'str' = 'fact', "
            "verification: 'str' = 'inferred-untested', "
            "verification_reason: 'str' = '', pain: 'int' = 0, "
            "pain_reason: 'str' = '', "
            "conflicts_with: 'tuple[str, ...]' = ()) -> None"
        ),
        DraftDiary: (
            "(date: 'str', outcomes: 'tuple[str, ...]' = (), "
            "decisions: 'tuple[str, ...]' = (), "
            "corrections: 'tuple[str, ...]' = (), "
            "open_loops: 'tuple[str, ...]' = (), events: 'str' = '') -> None"
        ),
        Draft: (
            "(notes: 'tuple[DraftNote, ...]' = (), "
            "diary: 'tuple[DraftDiary, ...]' = (), "
            "reflection: 'str' = '', "
            "escalate_to_human: 'tuple[str, ...]' = ()) -> None"
        ),
        parse_draft: "(text: 'str') -> 'Draft'",
        validate_draft: "(draft: 'Draft') -> 'list[str]'",
        procedure_warnings: "(draft: 'Draft') -> 'list[str]'",
        _parse_note: "(n: 'dict[str, Any]') -> 'DraftNote'",
        _parse_diary: "(d: 'dict[str, Any]') -> 'DraftDiary'",
        _str_list: "(value: 'Any') -> 'tuple[str, ...]'",
        DraftDiary.all_items: "(self) -> 'list[str]'",
    }
    assert {
        item: str(inspect.signature(item)) for item in expected_signatures
    } == expected_signatures

    assert [field.name for field in fields(DraftNote)] == [
        "title",
        "summary",
        "body",
        "tags",
        "kind",
        "verification",
        "verification_reason",
        "pain",
        "pain_reason",
        "conflicts_with",
    ]
    assert [field.name for field in fields(DraftDiary)] == [
        "date",
        "outcomes",
        "decisions",
        "corrections",
        "open_loops",
        "events",
    ]
    assert [field.name for field in fields(Draft)] == [
        "notes",
        "diary",
        "reflection",
        "escalate_to_human",
    ]
    assert {model.__module__ for model in (DraftNote, DraftDiary, Draft)} == {
        "trowel_py.memory.draft"
    }
    for instance, field_name in (
        (DraftNote("x"), "title"),
        (DraftDiary("2026-07-23"), "date"),
        (Draft(), "reflection"),
    ):
        with pytest.raises(FrozenInstanceError):
            setattr(instance, field_name, "changed")


def test_parse_uses_current_facade_dependencies(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, Any]] = []

    def loads(text: str) -> dict[str, Any]:
        calls.append(("loads", text))
        return {
            "notes": [{"title": "n"}],
            "diary": [{"date": "d"}],
            "reflection": "r",
        }

    def parse_note(value: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        calls.append(("note", value))
        return ("note", value)

    def parse_diary(value: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        calls.append(("diary", value))
        return ("diary", value)

    def build_draft(**values: Any) -> dict[str, Any]:
        calls.append(("draft", values))
        return values

    monkeypatch.setattr(
        draft_module,
        "json",
        SimpleNamespace(loads=loads),
    )
    monkeypatch.setattr(draft_module, "_parse_note", parse_note)
    monkeypatch.setattr(draft_module, "_parse_diary", parse_diary)
    monkeypatch.setattr(draft_module, "Draft", build_draft)

    assert draft_module.parse_draft("payload") == {
        "notes": (("note", {"title": "n"}),),
        "diary": (("diary", {"date": "d"}),),
        "reflection": "r",
        "escalate_to_human": (),
    }
    assert [name for name, _ in calls] == [
        "loads",
        "note",
        "diary",
        "draft",
    ]


def test_private_parsers_use_current_facade_dependencies(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        draft_module,
        "DraftNote",
        lambda **values: ("note", values),
    )
    parsed_note = cast(
        tuple[str, dict[str, Any]],
        draft_module._parse_note({"title": "x"}),
    )
    assert parsed_note[0] == "note"

    monkeypatch.setattr(
        draft_module,
        "DraftDiary",
        lambda **values: ("diary", values),
    )
    monkeypatch.setattr(
        draft_module,
        "_str_list",
        lambda value: ("normalized", str(value)),
    )
    parsed = draft_module._parse_diary({"date": "2026-07-23", "outcomes": ["x"]})
    assert parsed == (
        "diary",
        {
            "date": "2026-07-23",
            "outcomes": ("normalized", "['x']"),
            "decisions": ("normalized", "None"),
            "corrections": ("normalized", "None"),
            "open_loops": ("normalized", "None"),
            "events": "",
        },
    )


def test_validation_uses_current_facade_policy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    draft = Draft(
        notes=(
            DraftNote(
                title="x",
                kind="custom",
                verification="custom-tier",
            ),
        ),
        diary=(
            DraftDiary(
                date="2026-07-23",
                outcomes=("a", "b"),
            ),
        ),
    )
    monkeypatch.setattr(draft_module, "NOTE_KINDS", ("custom",))
    monkeypatch.setattr(
        draft_module,
        "VERIFICATION_TIERS",
        ("custom-tier",),
    )
    monkeypatch.setattr(draft_module, "EPISODE_MAX_ITEMS_PER_DATE", 1)
    monkeypatch.setattr(draft_module, "EPISODE_MAX_ITEMS_PER_FIELD", 1)
    monkeypatch.setattr(draft_module, "EPISODE_MAX_ITEM_CHARS", 0)
    monkeypatch.setattr(draft_module, "EPISODE_MAX_TOTAL_CHARS", 0)

    assert draft_module.validate_draft(draft) == [
        "diary[0]: too many structured items (2 > 1)",
        "diary[0].outcomes: too many items (2 > 1)",
        "diary[0].outcomes[0]: item exceeds 0 chars",
        "diary[0].outcomes[1]: item exceeds 0 chars",
        "diary[0]: structured text exceeds 0 chars (2)",
    ]


def test_procedure_warnings_use_current_facade_elements(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    draft = Draft(
        notes=(DraftNote(title="x", kind="procedure", body="contains needle"),)
    )
    monkeypatch.setattr(
        draft_module,
        "_PROCEDURE_ELEMENTS",
        {"custom": ("needle",)},
    )

    assert draft_module.procedure_warnings(draft) == []
