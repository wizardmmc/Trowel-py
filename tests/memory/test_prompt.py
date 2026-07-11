"""tests for the refine prompt 固化 (slice-040 T6).

These guard against prompt DRIFT, not LLM behavior. They assert the key
promises (S4 hard rule, three tiers, dual-track signals) are present in the
template text. LLM behavior is covered by the layer-2 benchmarks (T14).
"""
from __future__ import annotations

from trowel_py.memory.prompt import (
    DUALTRACK_SIGNAL_WORDS,
    NOTE_KINDS,
    VERIFICATION_TIERS,
    REFINE_PROMPT_TEMPLATE,
    build_refine_prompt,
)


def test_prompt_contains_three_tiers() -> None:
    # C-2: all three verification tiers appear in the prompt.
    for tier in VERIFICATION_TIERS:
        assert tier in REFINE_PROMPT_TEMPLATE


def test_prompt_contains_s4_pseudo_evidence_list() -> None:
    # C-3: the false-evidence list (things that do NOT substitute for root-cause
    # spike) is pinned, so the agent is not fooled by downstream data.
    assert "不替代" in REFINE_PROMPT_TEMPLATE
    assert "测试通过" in REFINE_PROMPT_TEMPLATE
    assert "commit 已落" in REFINE_PROMPT_TEMPLATE


def test_prompt_forbids_inferred_untested_stable() -> None:
    # C-2 hard rule: inferred-untested must never be elevated to stable.
    assert "inferred-untested" in REFINE_PROMPT_TEMPLATE
    assert "绝不" in REFINE_PROMPT_TEMPLATE


def test_prompt_lists_dualtrack_signal_words() -> None:
    # C-1: the agent's primary split instruction names the signal words.
    for word in ("我想到", "本质是", "方法论"):
        assert word in REFINE_PROMPT_TEMPLATE
    # the mirrored list the Python backstop uses is the same set
    assert DUALTRACK_SIGNAL_WORDS


def test_prompt_no_false_auto_satisfied_claim() -> None:
    """slice-040-c: the false 'step 1 already satisfied' claim is removed."""
    assert "已自动满足" not in REFINE_PROMPT_TEMPLATE
    assert "memory.search" in REFINE_PROMPT_TEMPLATE


def test_build_refine_prompt_fills_placeholders() -> None:
    p = build_refine_prompt("/x/y.jsonl", "tokens=100 turns=3 errors=1")
    assert "/x/y.jsonl" in p
    assert "tokens=100" in p
    assert "{jsonl_path}" not in p
    assert "{cost}" not in p


# ---------- slice-040-a: procedural memory (kind + four elements) ----------


def test_prompt_lists_note_kinds() -> None:
    # D3: every note gets a kind; all five kinds appear in the prompt so the
    # agent knows the menu.
    for kind in NOTE_KINDS:
        assert kind in REFINE_PROMPT_TEMPLATE


def test_prompt_guides_procedure_four_elements() -> None:
    # C-3: a kind=procedure note is guided to carry trigger/procedure/stop/
    # anti-pattern in its body.
    for elem in ("trigger", "procedure", "stop", "anti-pattern"):
        assert elem in REFINE_PROMPT_TEMPLATE


def test_prompt_asks_procedural_self_question() -> None:
    # the "what did I get stuck on / redo?" reflex that turns a painful session
    # into a reusable procedure.
    assert "卡了" in REFINE_PROMPT_TEMPLATE or "返工" in REFINE_PROMPT_TEMPLATE


def test_draft_schema_includes_kind() -> None:
    # the JSON schema shown to the agent carries the kind field.
    from trowel_py.memory.prompt import DRAFT_SCHEMA

    assert "kind" in DRAFT_SCHEMA
