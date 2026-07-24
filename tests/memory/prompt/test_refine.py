from __future__ import annotations

import trowel_py.memory.prompt as prompt_module
from trowel_py.memory.prompt import (
    DRAFT_SCHEMA,
    DUALTRACK_SIGNAL_WORDS,
    NOTE_KINDS,
    REFINE_PROMPT_TEMPLATE,
    VERIFICATION_TIERS,
    build_refine_prompt,
)


def test_prompt_contains_three_tiers() -> None:
    for tier in VERIFICATION_TIERS:
        assert tier in REFINE_PROMPT_TEMPLATE


def test_prompt_contains_s4_pseudo_evidence_list() -> None:
    assert "不替代" in REFINE_PROMPT_TEMPLATE
    assert "测试通过" in REFINE_PROMPT_TEMPLATE
    assert "commit 已落" in REFINE_PROMPT_TEMPLATE


def test_prompt_forbids_inferred_untested_stable() -> None:
    assert "inferred-untested" in REFINE_PROMPT_TEMPLATE
    assert "绝不" in REFINE_PROMPT_TEMPLATE


def test_prompt_lists_dualtrack_signal_words() -> None:
    for word in ("我想到", "本质是", "方法论"):
        assert word in REFINE_PROMPT_TEMPLATE
    assert DUALTRACK_SIGNAL_WORDS


def test_prompt_no_false_auto_satisfied_claim() -> None:
    assert "已自动满足" not in REFINE_PROMPT_TEMPLATE
    assert "memory.search" in REFINE_PROMPT_TEMPLATE


def test_build_refine_prompt_fills_placeholders() -> None:
    prompt = build_refine_prompt(
        "/x/y.jsonl",
        "tokens=100 turns=3 errors=1",
    )

    assert "/x/y.jsonl" in prompt
    assert "tokens=100" in prompt
    assert "{jsonl_path}" not in prompt
    assert "{cost}" not in prompt


def test_prompt_lists_note_kinds() -> None:
    for kind in NOTE_KINDS:
        assert kind in REFINE_PROMPT_TEMPLATE


def test_prompt_guides_procedure_four_elements() -> None:
    for element in ("trigger", "procedure", "stop", "anti-pattern"):
        assert element in REFINE_PROMPT_TEMPLATE


def test_prompt_asks_procedural_self_question() -> None:
    assert "卡了" in REFINE_PROMPT_TEMPLATE or "返工" in REFINE_PROMPT_TEMPLATE


def test_draft_schema_includes_kind() -> None:
    assert "kind" in DRAFT_SCHEMA


def test_draft_schema_diary_uses_four_lists() -> None:
    for kind in ("outcome", "decision", "correction", "open_loop", "evidence"):
        assert kind in DRAFT_SCHEMA


def test_episode_v2_prompt_has_source_refs_and_no_old_hard_compression() -> None:
    prompt = build_refine_prompt(
        "/x/source.numbered.jsonl",
        "tokens=1 turns=1 errors=0",
    )

    assert "source_refs" in prompt
    assert "before" in prompt and "after" in prompt
    assert "每个日期四类合计最多" not in prompt
    assert "1600" not in prompt


def test_refine_prompt_describes_four_diary_lists() -> None:
    for field in ("outcomes", "decisions", "corrections", "open_loops"):
        assert field in REFINE_PROMPT_TEMPLATE


def test_refine_prompt_forbids_agent_self_eval_in_diary() -> None:
    assert "自评" in REFINE_PROMPT_TEMPLATE or "绩效" in REFINE_PROMPT_TEMPLATE


def test_facade_functions_keep_module_identity() -> None:
    assert build_refine_prompt.__module__ == "trowel_py.memory.prompt"


def test_facade_refine_patches_flow_to_builder(monkeypatch) -> None:
    monkeypatch.setattr(
        prompt_module,
        "REFINE_PROMPT_TEMPLATE",
        "path={jsonl_path};cost={cost}",
    )

    prompt = build_refine_prompt("/patched.jsonl", "patched-cost")

    assert prompt == "path=/patched.jsonl;cost=patched-cost"
