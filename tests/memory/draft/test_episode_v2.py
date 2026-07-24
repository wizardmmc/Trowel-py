from __future__ import annotations

import json

import pytest

from trowel_py.memory.draft import (
    DraftCorrection,
    DraftDecision,
    DraftEvidence,
    DraftOpenLoop,
    DraftOutcome,
    parse_draft,
    validate_draft,
)


def _draft(*items: dict[str, object]) -> str:
    return json.dumps(
        {
            "diary": [
                {
                    "date": "2026-07-24",
                    "items": list(items),
                }
            ]
        }
    )


def test_parse_episode_v2_kind_specific_union() -> None:
    [entry] = parse_draft(
        _draft(
            {
                "kind": "outcome",
                "summary": "实现已完成",
                "detail": "窄测通过",
                "source_refs": ["L000001"],
            },
            {
                "kind": "decision",
                "summary": "继续使用 GLM-5.1",
                "reason": "长会话召回更完整",
                "status": "active",
                "source_refs": ["L000002"],
            },
            {
                "kind": "correction",
                "before": "Luna 足够",
                "after": "Episode 继续使用 GLM-5.1",
                "reason": "真实 A/B 中 GLM 赢 5 个样本",
                "source_refs": ["L000003", "L000004"],
            },
            {
                "kind": "open_loop",
                "summary": "补真实 Codex command journal smoke",
                "reason": "实验 corpus 没有 commandExecution",
                "status": "active",
                "source_refs": ["L000005"],
            },
            {
                "kind": "evidence",
                "summary": "14 次输出全部通过 schema gate",
                "detail": "两种模型均为 7/7",
                "source_refs": ["L000006"],
            },
        )
    ).diary

    assert [type(item) for item in entry.items] == [
        DraftOutcome,
        DraftDecision,
        DraftCorrection,
        DraftOpenLoop,
        DraftEvidence,
    ]
    assert entry.corrections == (
        "原来以为 Luna 足够，现确认 Episode 继续使用 GLM-5.1（依据：真实 A/B 中 GLM 赢 5 个样本）",
    )
    assert entry.open_loops == ("补真实 Codex command journal smoke（原因：实验 corpus 没有 commandExecution）",)


def test_validate_episode_v2_accepts_only_legal_source_refs() -> None:
    draft = parse_draft(
        _draft(
            {
                "kind": "outcome",
                "summary": "实现已完成",
                "detail": "",
                "source_refs": ["L000001", "L000002"],
            }
        )
    )

    assert validate_draft(draft, legal_source_refs={"L000001", "L000002"}) == []
    assert validate_draft(draft, legal_source_refs={"L000001"}) == [
        "diary[0].items[0].source_refs contains illegal refs: ['L000002']"
    ]


def test_validate_episode_v2_rejects_blank_source_ref() -> None:
    draft = parse_draft(
        _draft(
            {
                "kind": "outcome",
                "summary": "实现已完成",
                "detail": "",
                "source_refs": [" "],
            }
        )
    )
    assert validate_draft(draft) == [
        "diary[0].items[0].source_refs contains empty refs"
    ]


@pytest.mark.parametrize(
    ("item", "message"),
    [
        (
            {
                "kind": "decision",
                "summary": "选方案 A",
                "reason": "",
                "status": "completed",
                "source_refs": ["L000001"],
            },
            "reason must not be empty",
        ),
        (
            {
                "kind": "correction",
                "before": "",
                "after": "新结论",
                "reason": "证据",
                "source_refs": ["L000001"],
            },
            "before must not be empty",
        ),
        (
            {
                "kind": "open_loop",
                "summary": "补 smoke",
                "reason": "未覆盖",
                "status": "unknown",
                "source_refs": ["L000001"],
            },
            "status must be one of ['active', 'closed']",
        ),
    ],
)
def test_validate_episode_v2_enforces_kind_specific_fields(
    item: dict[str, object],
    message: str,
) -> None:
    errors = validate_draft(
        parse_draft(_draft(item)),
        legal_source_refs={"L000001"},
    )
    assert any(message in error for error in errors)


def test_parse_episode_v2_rejects_fields_from_another_kind() -> None:
    with pytest.raises(ValueError, match="outcome keys"):
        parse_draft(
            _draft(
                {
                    "kind": "outcome",
                    "summary": "完成",
                    "detail": "",
                    "status": "active",
                    "source_refs": ["L000001"],
                }
            )
        )
