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


# ---------- slice-062: structured experience track (four lists) ----------


def _structured_diary_json() -> str:
    """slice-062 contract 1: each date carries four nullable lists, not one
    free-text ``events`` blob."""
    return json.dumps(
        {
            "diary": [
                {
                    "date": "2026-07-17",
                    "outcomes": ["完成了 daily 重写", "验证到全量测试通过"],
                    "decisions": ["固定三问结构（进展/更正/待续）"],
                    "corrections": ["原来以为单 $ 零误伤 -> 实测就近配对吞整段"],
                    "open_loops": ["weekly 表达重写未做"],
                }
            ]
        }
    )


def test_parse_structured_diary_four_lists() -> None:
    d = parse_draft(_structured_diary_json())
    assert len(d.diary) == 1
    entry = d.diary[0]
    assert entry.date == "2026-07-17"
    assert entry.outcomes == ("完成了 daily 重写", "验证到全量测试通过")
    assert entry.decisions == ("固定三问结构（进展/更正/待续）",)
    assert entry.corrections == ("原来以为单 $ 零误伤 -> 实测就近配对吞整段",)
    assert entry.open_loops == ("weekly 表达重写未做",)


def test_parse_structured_diary_empty_lists_default() -> None:
    # an informational field is an empty list, never "无" prose (contract 1).
    d = parse_draft(json.dumps({"diary": [{"date": "2026-07-17"}]}))
    entry = d.diary[0]
    assert entry.outcomes == ()
    assert entry.decisions == ()
    assert entry.corrections == ()
    assert entry.open_loops == ()


def test_parse_legacy_events_still_readable() -> None:
    # contract 1: old ``events`` free text is still read (compat); new writes
    # go structured. A legacy draft round-trips its events untouched.
    d = parse_draft(json.dumps({"diary": [{"date": "2026-07-09", "events": "卡两小时在浏览器缓存"}]}))
    entry = d.diary[0]
    assert entry.events == "卡两小时在浏览器缓存"
    assert entry.outcomes == ()


def test_diary_all_items_concatenates_four_lists() -> None:
    # dualtrack scans every structured item, not just events.
    d = parse_draft(_structured_diary_json())
    entry = d.diary[0]
    items = entry.all_items()
    assert "完成了 daily 重写" in items
    assert "固定三问结构（进展/更正/待续）" in items
    assert "原来以为单 $ 零误伤 -> 实测就近配对吞整段" in items
    assert "weekly 表达重写未做" in items
    assert len(items) == 5  # 2 outcomes + 1 decision + 1 correction + 1 open_loop


def test_validate_accepts_structured_only_diary() -> None:
    # a date with structured lists and no events is a valid draft.
    d = parse_draft(_structured_diary_json())
    assert validate_draft(d) == []


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


# ---------- slice-040-a: procedural-memory soft check ----------


def test_procedure_note_with_four_elements_no_warning() -> None:
    # C-3 soft gate: a kind=procedure note whose body carries all four elements
    # (trigger / procedure / stop / anti-pattern, CN or EN) yields no warning.
    from trowel_py.memory.draft import procedure_warnings

    d = parse_draft(
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
    assert procedure_warnings(d) == []


def test_procedure_note_missing_elements_warned() -> None:
    # missing elements → warning returned (NOT a hard reject — D5 帮模型不约束).
    from trowel_py.memory.draft import procedure_warnings

    d = parse_draft(
        json.dumps(
            {
                "notes": [
                    {
                        "title": "半成品",
                        "kind": "procedure",
                        "verification": "verified",
                        "body": "trigger 出现时做点什么。",  # only trigger present
                    }
                ]
            }
        )
    )
    warns = procedure_warnings(d)
    assert any("'procedure'" in w for w in warns)
    assert any("'stop'" in w for w in warns)
    assert any("'anti-pattern'" in w for w in warns)


def test_non_procedure_note_not_checked() -> None:
    # fact / gotcha notes are never flagged for missing procedure elements.
    from trowel_py.memory.draft import procedure_warnings

    d = parse_draft(
        json.dumps(
            {
                "notes": [
                    {"title": "事实", "kind": "fact", "verification": "verified",
                     "body": "就是一句话。"}
                ]
            }
        )
    )
    assert procedure_warnings(d) == []


def test_procedure_empty_body_warned() -> None:
    from trowel_py.memory.draft import procedure_warnings

    d = parse_draft(
        json.dumps(
            {
                "notes": [
                    {"title": "空", "kind": "procedure", "verification": "verified",
                     "body": ""}
                ]
            }
        )
    )
    assert procedure_warnings(d)
