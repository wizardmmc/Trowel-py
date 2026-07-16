"""slice-050 profile distill prompt tests (prompt 固化).

Mirrors test_prompt.py's approach: assert the key promises are baked into the
prompt text (guard against prompt drift), not LLM behavior. Covers the session
path embed, the five-dim listing, the draft schema, the C-8 incremental-dedup
rules, cold-start marker, and embedding of the live profile + queue.
"""
from __future__ import annotations

from trowel_py.memory.profile_distill_prompt import (
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
    # the five profile titles must all appear so the agent knows the buckets
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
    # C-8: the agent must not re-propose what's already in the queue/profile
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
    # the verbatim schema shown to the agent is the JSON the agent should emit —
    # it must be a parseable object with the suggestion fields.
    import json

    obj = json.loads(SUGGESTIONS_DRAFT_SCHEMA)
    assert "suggestions" in obj
    keys = set(obj["suggestions"][0])
    assert {"dimension", "body", "sources", "rationale"} <= keys
