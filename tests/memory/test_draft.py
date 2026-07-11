"""tests for the draft schema gate (slice-040 T7)."""
from __future__ import annotations

import json

from trowel_py.memory.draft import parse_draft, validate_draft


def _valid_draft_json() -> str:
    return json.dumps(
        {
            "notes": [
                {
                    "title": "浏览器缓存导致 build 不生效",
                    "verification": "event-data-supported",
                    "pain": 3,
                    "tags": ["frontend"],
                }
            ],
            "diary": [{"date": "2026-07-09", "events": "卡两小时在浏览器缓存"}],
            "reflection": "无绕弯",
            "escalate_to_human": [],
        }
    )


def test_parse_valid_draft() -> None:
    d = parse_draft(_valid_draft_json())
    assert len(d.notes) == 1
    assert d.notes[0].title == "浏览器缓存导致 build 不生效"
    assert d.notes[0].verification == "event-data-supported"
    assert d.notes[0].tags == ("frontend",)
    assert len(d.diary) == 1
    assert d.diary[0].date == "2026-07-09"
    assert d.reflection == "无绕弯"


def test_parse_empty_draft() -> None:
    d = parse_draft("{}")
    assert d.notes == ()
    assert d.diary == ()
    assert d.reflection == ""


def test_validate_accepts_valid_draft() -> None:
    d = parse_draft(_valid_draft_json())
    assert validate_draft(d) == []


def test_validate_rejects_unknown_verification() -> None:
    # C-2: only the three legal tiers pass the gate.
    d = parse_draft(json.dumps({"notes": [{"title": "x", "verification": "probably-true"}]}))
    errors = validate_draft(d)
    assert any("unknown verification" in e for e in errors)


def test_validate_rejects_missing_title() -> None:
    d = parse_draft(json.dumps({"notes": [{"title": "", "verification": "verified"}]}))
    errors = validate_draft(d)
    assert any("missing title" in e for e in errors)


def test_validate_rejects_diary_missing_date() -> None:
    d = parse_draft(json.dumps({"diary": [{"date": "", "events": "x"}]}))
    errors = validate_draft(d)
    assert any("missing date" in e for e in errors)


def test_validate_accepts_inferred_untested() -> None:
    # inferred-untested is a LEGAL tier — it must NOT trip validation here.
    # The "must not be stable" rule is enforced one layer down in persist
    # (confidence is derived from verification there).
    d = parse_draft(json.dumps({"notes": [{"title": "x", "verification": "inferred-untested"}]}))
    assert validate_draft(d) == []
