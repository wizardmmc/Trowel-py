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


# ---------- slice-062: structured experience track (four lists) ----------


def test_draft_schema_diary_uses_four_lists() -> None:
    # contract 1: the diary schema shows the four structured lists.
    from trowel_py.memory.prompt import DRAFT_SCHEMA

    for field in ("outcomes", "decisions", "corrections", "open_loops"):
        assert field in DRAFT_SCHEMA


def test_refine_prompt_describes_four_diary_lists() -> None:
    # the agent is told what each list means so it routes correctly.
    for needle in ("outcomes", "decisions", "corrections", "open_loops"):
        assert needle in REFINE_PROMPT_TEMPLATE


def test_refine_prompt_forbids_agent_self_eval_in_diary() -> None:
    # C-5: agent self-evaluation / performance-review tone must stay out of the
    # experience track. The prompt names this ban.
    assert "自评" in REFINE_PROMPT_TEMPLATE or "绩效" in REFINE_PROMPT_TEMPLATE


def test_refine_prompt_keeps_item_target_below_hard_gate() -> None:
    # Real 2026-07-21 diary compression overshot an exact 180-char request.
    # Give the model a shorter writing target while Python keeps a wider gate.
    from trowel_py.memory.prompt import (
        EPISODE_MAX_ITEM_CHARS,
        EPISODE_TARGET_ITEM_CHARS,
    )

    p = build_refine_prompt("/x/y.jsonl", "tokens=1 turns=1 errors=0")

    assert EPISODE_TARGET_ITEM_CHARS < EPISODE_MAX_ITEM_CHARS
    assert f"尽量控制在 {EPISODE_TARGET_ITEM_CHARS} 字以内" in p
    assert f"硬上限 {EPISODE_MAX_ITEM_CHARS} 字" in p


# ---------- slice-062: daily compression prompt (structured I/O) ----------


def test_daily_compress_prompt_requires_source_per_item() -> None:
    # contract 4 / C-6: every daily item must cite a source segment id.
    from trowel_py.memory.prompt import DAILY_COMPRESS_TEMPLATE

    assert "source" in DAILY_COMPRESS_TEMPLATE
    assert "segment" in DAILY_COMPRESS_TEMPLATE


def test_daily_compress_prompt_states_budget_priority() -> None:
    # contract 4: corrections + open loops outrank outcomes/decisions when trimming.
    from trowel_py.memory.prompt import DAILY_COMPRESS_TEMPLATE

    assert "更正" in DAILY_COMPRESS_TEMPLATE
    assert "待续" in DAILY_COMPRESS_TEMPLATE
    assert "800" in DAILY_COMPRESS_TEMPLATE


def test_daily_items_schema_has_type_text_source() -> None:
    from trowel_py.memory.prompt import DAILY_ITEMS_SCHEMA

    assert '"type"' in DAILY_ITEMS_SCHEMA
    assert '"text"' in DAILY_ITEMS_SCHEMA
    assert '"source"' in DAILY_ITEMS_SCHEMA


def test_daily_compress_prompt_bans_self_eval_and_flow() -> None:
    # C-5 + the delete list: agent self-eval, tool-call flow, resolved blockers
    # are named so the model keeps them out of the daily.
    from trowel_py.memory.prompt import DAILY_COMPRESS_TEMPLATE

    assert "自评" in DAILY_COMPRESS_TEMPLATE
    assert "工具调用" in DAILY_COMPRESS_TEMPLATE or "逐轮" in DAILY_COMPRESS_TEMPLATE
    assert "已解决" in DAILY_COMPRESS_TEMPLATE


def test_build_daily_compress_prompt_fills_placeholders() -> None:
    from trowel_py.memory.prompt import build_daily_compress_prompt

    p = build_daily_compress_prompt(
        date="2026-07-17",
        sources_block="【seg-1】outcomes: 完成了 X",
    )
    assert "2026-07-17" in p
    assert "seg-1" in p
    assert "{date}" not in p
    assert "{sources_block}" not in p
