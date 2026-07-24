"""使用 fake host 验证 todo 六步扩展，不启动真实 Claude。"""

from __future__ import annotations

import json

import pytest

from trowel_py.todo_loop.expansion import (
    Assumption,
    ExpansionResult,
    build_expansion_prompt,
    expand_todo,
    parse_expansion,
)


class FakeHost:
    def __init__(self, reply: str) -> None:
        self.reply = reply
        self.last_prompt: str | None = None

    def send(self, message: str) -> str:
        self.last_prompt = message
        return self.reply


def _cc_json(**overrides: object) -> str:
    payload: dict[str, object] = {
        "recap": "我理解你要在 tcc 里发图给 cc",
        "candidates": ["tcc 发图", "独立 chat 发图", "复习卡片带图"],
        "assumptions": [
            {"text": "cc stream-json 支持 image block", "has_anchor": True},
            {"text": "用户主要在 tcc 发图", "has_anchor": False},
        ],
        "acceptance_criteria": ["粘贴截图 cc 能读出报错文字"],
        "confidence": "medium",
        "confidence_reason": "技术链路清但图片持久化未定",
    }
    payload.update(overrides)
    return json.dumps(payload, ensure_ascii=False)


def test_prompt_embeds_todo_and_six_steps() -> None:
    prompt = build_expansion_prompt("增加图片输入的功能")
    assert "增加图片输入的功能" in prompt
    assert "画像" in prompt and "system" in prompt
    assert "识别歧义" in prompt
    assert "记忆反证" in prompt
    assert "枚举" in prompt
    assert "candidates" in prompt or "候选" in prompt


def test_prompt_forbids_locking_first_interpretation() -> None:
    prompt = build_expansion_prompt("重构后端")
    assert "锁" in prompt


def test_prompt_carries_three_tier_collection_shape() -> None:
    prompt = build_expansion_prompt("x")
    assert "镜像" in prompt
    assert "执行计划" in prompt


def test_parse_well_formed_output() -> None:
    r = parse_expansion(_cc_json())
    assert isinstance(r, ExpansionResult)
    assert r.recap == "我理解你要在 tcc 里发图给 cc"
    assert r.candidates == ("tcc 发图", "独立 chat 发图", "复习卡片带图")
    assert r.assumptions[0] == Assumption("cc stream-json 支持 image block", True)
    assert r.assumptions[1].has_anchor is False
    assert r.confidence == "medium"
    assert r.acceptance_criteria == ("粘贴截图 cc 能读出报错文字",)


@pytest.mark.parametrize(
    "bad_confidence",
    [None, True, False, 3, 0, "", "very high", "HIGH", {"a": "b"}, [1]],
)
def test_parse_confidence_only_three_levels(bad_confidence: object) -> None:
    r = parse_expansion(_cc_json(confidence=bad_confidence))
    assert r.confidence == "low"


def test_parse_strips_whitespace_around_confidence() -> None:
    r = parse_expansion(_cc_json(confidence="  high  "))
    assert r.confidence == "high"


def test_parse_assumption_missing_anchor_defaults_pure_guess() -> None:
    r = parse_expansion(_cc_json(assumptions=[{"text": "没标 anchor 的假设"}]))
    assert r.assumptions == (Assumption("没标 anchor 的假设", False),)


def test_parse_assumption_string_false_not_treated_as_true() -> None:
    r = parse_expansion(_cc_json(assumptions=[{"text": "x", "has_anchor": "false"}]))
    assert r.assumptions == (Assumption("x", False),)


def test_parse_broken_json_does_not_raise() -> None:
    r = parse_expansion("这不是 json {{{")
    assert r.confidence == "low"
    assert "失败" in r.confidence_reason


def test_parse_empty_cc_output_degrades() -> None:
    r = parse_expansion("")
    assert r.confidence == "low"
    assert "失败" in r.confidence_reason


def test_parse_top_level_not_dict_degrades() -> None:
    r = parse_expansion(json.dumps([1, 2, 3]))
    assert r.confidence == "low"
    assert "不是对象" in r.confidence_reason


def test_parse_empty_dict_all_defaults() -> None:
    r = parse_expansion("{}")
    assert r.recap == ""
    assert r.candidates == ()
    assert r.assumptions == ()
    assert r.confidence == "low"
    assert r.confidence_reason == "cc 未给出置信度理由"


def test_parse_missing_fields_default_empty_and_low() -> None:
    r = parse_expansion(json.dumps({"recap": "仅复述"}, ensure_ascii=False))
    assert r.recap == "仅复述"
    assert r.candidates == ()
    assert r.assumptions == ()
    assert r.confidence == "low"


def test_parse_candidates_non_str_items_skipped() -> None:
    r = parse_expansion(_cc_json(candidates=["tcc 发图", 42, None, "独立 chat", ""]))
    assert r.candidates == ("tcc 发图", "独立 chat")


def test_parse_candidates_non_list_degrades() -> None:
    r = parse_expansion(_cc_json(candidates="不是 list"))
    assert r.candidates == ()


def test_parse_acceptance_criteria_non_str_items_skipped() -> None:
    r = parse_expansion(_cc_json(acceptance_criteria=["c1", 42, None, "c2"]))
    assert r.acceptance_criteria == ("c1", "c2")


def test_parse_assumptions_non_list_degrades() -> None:
    r = parse_expansion(_cc_json(assumptions="not a list"))
    assert r.assumptions == ()


def test_parse_recap_non_str_defaults_empty() -> None:
    r = parse_expansion(_cc_json(recap=123))
    assert r.recap == ""


def test_expand_wires_prompt_to_parse() -> None:
    host = FakeHost(_cc_json())
    r = expand_todo("增加图片输入的功能", host)
    assert host.last_prompt is not None
    assert "增加图片输入的功能" in host.last_prompt
    assert r.confidence == "medium"
    assert len(r.candidates) == 3


def test_expand_real_fixture_exit_plan_mode_bug() -> None:
    diagnosis = json.dumps(
        {
            "recap": "ExitPlanMode 的 control_request 被 translator 吞掉，cc 卡死等超时",
            "candidates": [
                "translator 吞 control_request",
                "走普通 tool 路径",
                "cc 自身 bug",
            ],
            "assumptions": [
                {
                    "text": "translator.py:480 硬过滤非 AskUserQuestion",
                    "has_anchor": True,
                }
            ],
            "acceptance_criteria": ["ExitPlanMode 时 cc 不再卡死"],
            "confidence": "high",
            "confidence_reason": "代码注释 + docstring 自证是 TODO 未实现",
        },
        ensure_ascii=False,
    )
    r = expand_todo("ExitPlanMode的时候卡住了", FakeHost(diagnosis))
    assert r.confidence == "high"
    assert r.candidates[0] == "translator 吞 control_request"


def test_expand_real_fixture_refactor_backend_low_confidence_mirror() -> None:
    mirror = json.dumps(
        {
            "recap": "「重构后端」范围太大，可能指拆大文件/memory 重组/补测试等",
            "candidates": ["拆大文件", "memory 重组", "补测试", "质量清理"],
            "assumptions": [{"text": "用户没给范围和目的", "has_anchor": False}],
            "acceptance_criteria": [],
            "confidence": "low",
            "confidence_reason": "范围未定，done-signal 钉不死",
        },
        ensure_ascii=False,
    )
    r = expand_todo("重构后端", FakeHost(mirror))
    assert r.confidence == "low"
    assert len(r.candidates) >= 3
