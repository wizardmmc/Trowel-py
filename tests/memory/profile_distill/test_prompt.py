from __future__ import annotations

import pytest

from trowel_py.memory.profile_distill.prompt import (
    SUGGESTIONS_DRAFT_SCHEMA,
    build_distill_prompt,
)
from trowel_py.memory.types import Profile, Suggestion


def _profile(**dims: str) -> Profile:
    return Profile(updated="2026-07-14", **dims)


def test_prompt_embeds_session_jsonl_path() -> None:
    p = build_distill_prompt("/x/y.jsonl", [], _profile())
    assert "/x/y.jsonl" in p


def test_prompt_lists_five_dimensions() -> None:
    p = build_distill_prompt("/x.jsonl", [], _profile())
    for title in ("能力水平", "方法论偏好", "表达风格", "长程目标", "其他"):
        assert title in p


def test_prompt_embeds_draft_schema() -> None:
    p = build_distill_prompt("/x.jsonl", [], _profile())
    assert "suggestions-draft.json" in p
    assert "dimension" in p
    assert "rationale" in p
    assert "sources" in p


def test_prompt_carries_incremental_dedup_rules() -> None:
    p = build_distill_prompt("/x.jsonl", [], _profile())
    assert "不产重复" in p
    assert "宁缺毋滥" in p


def test_prompt_cold_start_marker_when_profile_empty() -> None:
    p = build_distill_prompt("/x.jsonl", [], Profile())
    assert "冷启动" in p


def test_prompt_embeds_existing_profile_content() -> None:
    p = build_distill_prompt(
        "/x.jsonl", [], _profile(ability="网安硕士", goal="反诈论文")
    )
    assert "网安硕士" in p
    assert "反诈论文" in p


def test_prompt_embeds_existing_suggestions() -> None:
    existing = [
        Suggestion(
            id="s1",
            dimension="ability",
            body="会 FastAPI",
            sources=(),
            date="2026-07-14",
        ),
    ]
    p = build_distill_prompt("/x.jsonl", existing, _profile())
    assert "会 FastAPI" in p


def test_prompt_empty_queue_marker() -> None:
    p = build_distill_prompt("/x.jsonl", [], _profile())
    assert "队列为空" in p


def test_prompt_incremental_range_header() -> None:
    p = build_distill_prompt(
        "/x.jsonl", [], _profile(), start_offset=1024, end_offset=2048
    )
    assert "增量范围" in p
    assert "1024" in p
    assert "2048" in p


def test_schema_is_valid_json_shell() -> None:
    import json

    obj = json.loads(SUGGESTIONS_DRAFT_SCHEMA)
    assert "suggestions" in obj
    keys = set(obj["suggestions"][0])
    assert {"dimension", "body", "sources", "rationale"} <= keys


_V2_RULE_PHRASES = [
    ("保守归因", "不能证明已经掌握"),
    ("保守归因-正在学习", "正在学习"),
    ("主体隔离", "不得归为用户能力"),
    ("稳定性门槛-长期偏好", "长期偏好"),
    ("稳定性门槛-两场景", "两个独立场景"),
    ("反证优先", "反证"),
    ("反证优先-更保守", "更保守"),
    ("使用价值", "实际改变"),
    ("能力证据-自述", "明确自述"),
    ("能力证据-可核验", "可核验"),
    ("目标时效", "长期或持续性"),
    ("原子短句-一个结论", "一个结论"),
    ("原子短句-不放例子", "不放例子"),
    ("数量上限", "最多产 2 条"),
    ("数量上限-排序", "从高到低"),
    ("长度上限", "60 个 Unicode 字符"),
]


@pytest.mark.parametrize("name, phrase", _V2_RULE_PHRASES)
def test_prompt_carries_v2_hard_rule(name: str, phrase: str) -> None:
    p = build_distill_prompt("/x.jsonl", [], _profile())
    assert phrase in p, f"v2 hard rule {name!r} missing its phrase {phrase!r}"


def test_prompt_v2_forbids_ability_from_questions() -> None:
    p = build_distill_prompt("/x.jsonl", [], _profile())
    assert "追问得深入" in p
    assert "不能代替能力证据" in p


def test_prompt_v2_forbids_overclaim_words() -> None:
    p = build_distill_prompt("/x.jsonl", [], _profile())
    assert "研究级" in p
    assert "精通" in p


def test_prompt_v2_schema_body_caps_at_60_chars() -> None:
    assert "不超过 60 个 Unicode 字符" in SUGGESTIONS_DRAFT_SCHEMA


def test_prompt_v2_self_check_block_present() -> None:
    p = build_distill_prompt("/x.jsonl", [], _profile())
    assert "输出前自检" in p
    for q in ("把 AI 的劳动算给了用户", "正在问", "偶然选择", "去掉例子和赞美后"):
        assert q in p
