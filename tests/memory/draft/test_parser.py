from __future__ import annotations

import json

import pytest

from tests.memory.draft.support import (
    structured_diary_json,
    valid_draft_json,
)
from trowel_py.memory.draft import parse_draft


def test_parse_valid_draft() -> None:
    draft = parse_draft(valid_draft_json())
    assert len(draft.notes) == 1
    assert draft.notes[0].title == "浏览器缓存导致 build 不生效"
    assert draft.notes[0].verification == "event-data-supported"
    assert draft.notes[0].tags == ("frontend",)
    assert len(draft.diary) == 1
    assert draft.diary[0].date == "2026-07-09"
    assert draft.reflection == "无绕弯"


def test_parse_empty_draft() -> None:
    draft = parse_draft("{}")
    assert draft.notes == ()
    assert draft.diary == ()
    assert draft.reflection == ""


def test_parse_structured_diary_four_lists() -> None:
    [entry] = parse_draft(structured_diary_json()).diary
    assert entry.date == "2026-07-17"
    assert entry.outcomes == ("完成了 daily 重写", "验证到全量测试通过")
    assert entry.decisions == ("固定三问结构（进展/更正/待续）",)
    assert entry.corrections == ("原来以为单 $ 零误伤 -> 实测就近配对吞整段",)
    assert entry.open_loops == ("weekly 表达重写未做",)


def test_parse_structured_diary_empty_lists_default() -> None:
    [entry] = parse_draft(json.dumps({"diary": [{"date": "2026-07-17"}]})).diary
    assert entry.outcomes == ()
    assert entry.decisions == ()
    assert entry.corrections == ()
    assert entry.open_loops == ()


def test_parse_legacy_events_still_readable() -> None:
    [entry] = parse_draft(
        json.dumps(
            {
                "diary": [
                    {
                        "date": "2026-07-09",
                        "events": "卡两小时在浏览器缓存",
                    }
                ]
            }
        )
    ).diary
    assert entry.events == "卡两小时在浏览器缓存"
    assert entry.outcomes == ()


def test_diary_all_items_concatenates_four_lists() -> None:
    [entry] = parse_draft(structured_diary_json()).diary
    assert entry.all_items() == [
        "完成了 daily 重写",
        "验证到全量测试通过",
        "固定三问结构（进展/更正/待续）",
        "原来以为单 $ 零误伤 -> 实测就近配对吞整段",
        "weekly 表达重写未做",
    ]


def test_parse_keeps_legacy_coercion_edges() -> None:
    draft = parse_draft(
        json.dumps(
            {
                "notes": [
                    {
                        "title": None,
                        "summary": None,
                        "tags": "ab",
                        "pain": False,
                    }
                ],
                "diary": [
                    {
                        "date": None,
                        "outcomes": [" a ", "", None, 0, False],
                        "decisions": "not-a-list",
                    }
                ],
                "reflection": None,
                "escalate_to_human": "ab",
            }
        )
    )
    [note] = draft.notes
    [diary] = draft.diary
    assert note.title == "None"
    assert note.summary == "None"
    assert note.tags == ("a", "b")
    assert note.pain == 0
    assert diary.date == "None"
    assert diary.outcomes == ("a", "None", "0", "False")
    assert diary.decisions == ()
    assert draft.reflection == ""
    assert draft.escalate_to_human == ("a", "b")


@pytest.mark.parametrize(
    ("text", "exception"),
    [
        ("not-json", json.JSONDecodeError),
        ("null", AttributeError),
        ("[]", AttributeError),
        ('{"notes": 1}', TypeError),
        ('{"notes": [1]}', AttributeError),
    ],
)
def test_parse_keeps_existing_error_types(
    text: str,
    exception: type[Exception],
) -> None:
    with pytest.raises(exception):
        parse_draft(text)
