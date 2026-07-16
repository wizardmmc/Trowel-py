"""tests for the judge prompt 固化 (slice-053).

These guard against prompt DRIFT, not LLM behavior. They assert the key
promises (three dimensions, two-miss attribution, reason+evidence, no
fabricated memory_id) are present in the template text. LLM behavior is
covered by the layer-2 / E2E check (a real spawn over historical sessions).
"""
from __future__ import annotations

from trowel_py.memory.judge_prompt import (
    JUDGE_PROMPT_TEMPLATE,
    JUDGE_SCHEMA,
    build_judge_prompt,
)


def test_prompt_names_three_dimensions() -> None:
    # slice-053: the three judgement dimensions are all named.
    for needle in ("用了没用", "有用没用", "该用没用"):
        assert needle in JUDGE_PROMPT_TEMPLATE


def test_prompt_names_two_miss_attributions() -> None:
    # C-7: recall-miss carries the two attributions that count as a miss.
    assert "retrieval_miss" in JUDGE_PROMPT_TEMPLATE
    assert "awareness_miss" in JUDGE_PROMPT_TEMPLATE


def test_prompt_excludes_novelty_from_miss() -> None:
    # C-7: "no relevant note existed" is NOT a miss (it points to write, not
    # to retrieval/injection). The prompt must say so.
    assert "不算" in JUDGE_PROMPT_TEMPLATE or "不算 miss" in JUDGE_PROMPT_TEMPLATE


def test_prompt_requires_reason_and_evidence() -> None:
    # C-4: every judgement must carry reason + evidence (traceable, not sampled).
    assert "reason" in JUDGE_PROMPT_TEMPLATE
    assert "evidence" in JUDGE_PROMPT_TEMPLATE


def test_prompt_forbids_fabricated_memory_id() -> None:
    # C-6: the agent must not invent memory_ids; fabricated ones are dropped.
    assert "不许编造" in JUDGE_PROMPT_TEMPLATE
    assert "memory_id" in JUDGE_PROMPT_TEMPLATE


def test_prompt_feeds_hard_access_evidence() -> None:
    # C-3: the judged session's access-log is pre-extracted by Python (filtered
    # by its cc_session_id) and fed as hard evidence — the judge does NOT paw
    # the log files itself.
    assert "检索记录" in JUDGE_PROMPT_TEMPLATE or "access" in JUDGE_PROMPT_TEMPLATE.lower()


def test_schema_has_hits_recall_miss_summary() -> None:
    # the draft schema the agent must emit.
    assert '"hits"' in JUDGE_SCHEMA
    assert '"recall_miss"' in JUDGE_SCHEMA
    assert '"summary"' in JUDGE_SCHEMA
    # a hit carries used + outcome + reason + evidence
    assert '"used"' in JUDGE_SCHEMA
    assert '"outcome"' in JUDGE_SCHEMA
    # a miss carries attribution + reason + evidence
    assert '"attribution"' in JUDGE_SCHEMA


def test_build_judge_prompt_fills_placeholders() -> None:
    p = build_judge_prompt(
        jsonl_path="/x/y.jsonl",
        access_log_summary="search: 3 条; read: note-aaa",
        dictionary_index="note-aaa: 红队背景",
    )
    assert "/x/y.jsonl" in p
    assert "note-aaa" in p
    assert "{jsonl_path}" not in p
    assert "{access_log_summary}" not in p
    assert "{dictionary_index}" not in p
