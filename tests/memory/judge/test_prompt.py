from __future__ import annotations

from hashlib import sha256
from typing import get_args

from trowel_py.memory.access_log import Outcome as AccessOutcome
from trowel_py.memory.judge_prompt import (
    HIT_OUTCOMES,
    JUDGE_PROMPT_TEMPLATE,
    JUDGE_SCHEMA,
    MISS_ATTRIBUTIONS,
    build_judge_prompt,
)
from trowel_py.memory.judgements import (
    VALID_ATTRIBUTIONS,
    VALID_OUTCOMES,
    Attribution,
    Outcome as JudgementOutcome,
)


def test_prompt_names_three_dimensions() -> None:
    for needle in ("用了没用", "有用没用", "该用没用"):
        assert needle in JUDGE_PROMPT_TEMPLATE


def test_prompt_names_two_miss_attributions() -> None:
    assert "retrieval_miss" in JUDGE_PROMPT_TEMPLATE
    assert "awareness_miss" in JUDGE_PROMPT_TEMPLATE


def test_prompt_excludes_novelty_from_miss() -> None:
    assert "不算" in JUDGE_PROMPT_TEMPLATE or "不算 miss" in JUDGE_PROMPT_TEMPLATE


def test_prompt_requires_reason_and_evidence() -> None:
    assert "reason" in JUDGE_PROMPT_TEMPLATE
    assert "evidence" in JUDGE_PROMPT_TEMPLATE


def test_prompt_forbids_fabricated_memory_id() -> None:
    assert "不许编造" in JUDGE_PROMPT_TEMPLATE
    assert "memory_id" in JUDGE_PROMPT_TEMPLATE


def test_prompt_feeds_hard_access_evidence() -> None:
    assert (
        "检索记录" in JUDGE_PROMPT_TEMPLATE or "access" in JUDGE_PROMPT_TEMPLATE.lower()
    )


def test_schema_has_hits_recall_miss_summary() -> None:
    assert '"hits"' in JUDGE_SCHEMA
    assert '"recall_miss"' in JUDGE_SCHEMA
    assert '"summary"' in JUDGE_SCHEMA
    assert '"used"' in JUDGE_SCHEMA
    assert '"outcome"' in JUDGE_SCHEMA
    assert '"attribution"' in JUDGE_SCHEMA


def test_prompt_vocabularies_are_frozen() -> None:
    assert MISS_ATTRIBUTIONS == ("retrieval_miss", "awareness_miss")
    assert HIT_OUTCOMES == ("helpful", "harmful", "unused", "unknown")
    assert MISS_ATTRIBUTIONS == get_args(Attribution)
    assert HIT_OUTCOMES == get_args(JudgementOutcome) == get_args(AccessOutcome)
    assert frozenset(MISS_ATTRIBUTIONS) == VALID_ATTRIBUTIONS
    assert frozenset(HIT_OUTCOMES) == VALID_OUTCOMES


def test_prompt_and_schema_bytes_are_frozen() -> None:
    assert sha256(JUDGE_SCHEMA.encode("utf-8")).hexdigest() == (
        "b4df088536a5ec71a3590ca9d270bfe8e87c6f34a1df1c9e7d83c64d454530d5"
    )
    assert sha256(JUDGE_PROMPT_TEMPLATE.encode("utf-8")).hexdigest() == (
        "1dd40bacb97581f9ac8c887f15e93636219adbe71ff182c93f9f40521f99b375"
    )


def test_build_judge_prompt_fills_placeholders() -> None:
    p = build_judge_prompt(
        jsonl_path="/x/y.jsonl",
        access_log_summary="search: 3 条; read: note-aaa",
        dictionary_index="note-aaa: 缓存一致性经验",
    )
    assert "/x/y.jsonl" in p
    assert "note-aaa" in p
    assert "{jsonl_path}" not in p
    assert "{access_log_summary}" not in p
    assert "{dictionary_index}" not in p
