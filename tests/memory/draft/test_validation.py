from __future__ import annotations

import json

from tests.memory.draft.support import (
    structured_diary_json,
    valid_draft_json,
)
from trowel_py.memory.draft import (
    Draft,
    DraftDiary,
    DraftNote,
    parse_draft,
    procedure_warnings,
    validate_draft,
)


def test_validate_rejects_legacy_events_in_new_draft() -> None:
    draft = parse_draft(
        json.dumps({"diary": [{"date": "2026-07-09", "events": "长" * 2000}]})
    )
    errors = validate_draft(draft)
    assert any("legacy events are not allowed" in error for error in errors)


def test_validate_accepts_structured_only_diary() -> None:
    assert validate_draft(parse_draft(structured_diary_json())) == []


def test_validate_does_not_cap_episode_item_count() -> None:
    item = "完成当天关键实现并通过相关测试"
    draft = parse_draft(
        json.dumps(
            {
                "diary": [
                    {
                        "date": "2026-07-20",
                        "items": [
                            {
                                "kind": "outcome",
                                "summary": f"{item}{index}",
                                "detail": "",
                                "source_refs": [f"L{index + 1:06d}"],
                            }
                            for index in range(30)
                        ],
                    }
                ]
            }
        )
    )
    assert validate_draft(draft) == []


def test_validate_does_not_apply_old_item_character_cap() -> None:
    draft = parse_draft(
        json.dumps(
            {
                "diary": [
                    {
                        "date": "2026-07-20",
                        "items": [
                            {
                                "kind": "outcome",
                                "summary": "长" * 500,
                                "detail": "",
                                "source_refs": ["L000001"],
                            }
                        ],
                    }
                ]
            }
        )
    )
    assert validate_draft(draft) == []


def test_validate_accepts_valid_draft() -> None:
    assert validate_draft(parse_draft(valid_draft_json())) == []


def test_validate_rejects_unknown_verification() -> None:
    draft = parse_draft(
        json.dumps({"notes": [{"title": "x", "verification": "probably-true"}]})
    )
    errors = validate_draft(draft)
    assert any("unknown verification" in error for error in errors)


def test_validate_rejects_unknown_feedback_kind() -> None:
    draft = parse_draft(
        json.dumps(
            {
                "notes": [
                    {
                        "title": "未知 kind 应在落盘前被拒绝",
                        "kind": "feedback",
                        "verification": "event-data-supported",
                    }
                ]
            }
        )
    )
    errors = validate_draft(draft)
    assert any("unknown kind 'feedback'" in error for error in errors)


def test_validate_rejects_missing_title() -> None:
    draft = parse_draft(
        json.dumps({"notes": [{"title": "", "verification": "verified"}]})
    )
    errors = validate_draft(draft)
    assert any("missing title" in error for error in errors)


def test_validate_rejects_diary_missing_date() -> None:
    draft = parse_draft(json.dumps({"diary": [{"date": "", "outcomes": ["完成了 X"]}]}))
    errors = validate_draft(draft)
    assert any("missing date" in error for error in errors)


def test_validate_accepts_inferred_untested() -> None:
    draft = parse_draft(
        json.dumps(
            {
                "notes": [
                    {
                        "title": "x",
                        "verification": "inferred-untested",
                    }
                ]
            }
        )
    )
    assert validate_draft(draft) == []


def test_validate_accumulates_errors_in_stable_order() -> None:
    draft = Draft(
        notes=(DraftNote(title=" ", kind="bad", verification="wrong"),),
        diary=(
            DraftDiary(
                date=" ",
                events="legacy",
                outcomes=("x" * 201, "a", "b", "c"),
            ),
        ),
    )
    assert validate_draft(draft) == [
        "notes[0]: missing title",
        (
            "notes[0] ' ': unknown kind 'bad'; expected one of "
            "['fact', 'gotcha', 'procedure', 'preference', 'hypothesis']"
        ),
        "notes[0] ' ': unknown verification 'wrong'",
        "diary[0]: missing date",
        (
            "diary[0]: legacy events are not allowed in a new draft; "
            "use items"
        ),
        (
            "diary[0]: legacy structured lists are not allowed in a new "
            "draft; use items"
        ),
    ]


def test_validate_rejects_legacy_structured_lists_in_new_draft() -> None:
    draft = Draft(
        diary=(DraftDiary(date="2026-07-20", outcomes=("旧格式",)),)
    )
    assert validate_draft(draft) == [
        (
            "diary[0]: legacy structured lists are not allowed in a new "
            "draft; use items"
        )
    ]


def test_procedure_note_with_four_elements_no_warning() -> None:
    draft = parse_draft(
        json.dumps(
            {
                "notes": [
                    {
                        "title": "build 不生效",
                        "kind": "procedure",
                        "verification": "verified",
                        "body": (
                            "trigger: build 不生效。\n"
                            "procedure: 先 hard-refresh。\n"
                            "stop: 看到 200 响应即停。\n"
                            "anti-pattern: 别只重启 dev server。"
                        ),
                    }
                ]
            }
        )
    )
    assert procedure_warnings(draft) == []


def test_procedure_note_missing_elements_warned() -> None:
    draft = parse_draft(
        json.dumps(
            {
                "notes": [
                    {
                        "title": "半成品",
                        "kind": "procedure",
                        "verification": "verified",
                        "body": "trigger 出现时做点什么。",
                    }
                ]
            }
        )
    )
    assert procedure_warnings(draft) == [
        ("notes[0] '半成品': kind=procedure but body may miss 'procedure'"),
        "notes[0] '半成品': kind=procedure but body may miss 'stop'",
        ("notes[0] '半成品': kind=procedure but body may miss 'anti-pattern'"),
    ]


def test_non_procedure_note_not_checked() -> None:
    draft = parse_draft(
        json.dumps(
            {
                "notes": [
                    {
                        "title": "事实",
                        "kind": "fact",
                        "verification": "verified",
                        "body": "就是一句话。",
                    }
                ]
            }
        )
    )
    assert procedure_warnings(draft) == []


def test_procedure_empty_body_warned() -> None:
    draft = parse_draft(
        json.dumps(
            {
                "notes": [
                    {
                        "title": "空",
                        "kind": "procedure",
                        "verification": "verified",
                        "body": "",
                    }
                ]
            }
        )
    )
    assert procedure_warnings(draft)
