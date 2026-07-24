"""在 CC 会话中执行 todo 六步理解，并解析为结构化结果。

调用方负责提供已注入 profile 与 memory 的 host。解析失败、字段缺失或非法
confidence 只降级为低置信结果；宽松解析不能把非字符串脏值强制转成字符串。
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Literal, Protocol

logger = logging.getLogger(__name__)

Confidence = Literal["high", "medium", "low"]
_VALID_CONFIDENCE: frozenset[str] = frozenset({"high", "medium", "low"})


@dataclass(frozen=True)
class Assumption:
    """显式假设；只有真正的布尔 ``True`` 才表示存在代码或 memory 锚点。"""

    text: str
    has_anchor: bool


@dataclass(frozen=True)
class ExpansionResult:
    recap: str
    candidates: tuple[str, ...]
    assumptions: tuple[Assumption, ...]
    acceptance_criteria: tuple[str, ...]
    confidence: Confidence
    confidence_reason: str


class CCHost(Protocol):
    def send(self, message: str) -> str: ...


def build_expansion_prompt(todo_text: str) -> str:
    """依赖 CC system prompt 中已有的 profile 与 memory，不在此重复注入。"""
    return (
        "你在模拟 trowel 张六步理解层的一次执行。你 system prompt 里已有用户画像和 "
        "memory——按它理解用户意图。\n\n"
        f"【todo（用户原话）】\n{todo_text}\n\n"
        "强制六步，不许跳步，**不许锁定第一个解释就输出**"
        "（spike 教训：锁第一个=李四=全错）：\n"
        "1【识别歧义】列出 todo 关键词在项目里可能指的所有东西，自己枚举，别只盯第一个。\n"
        "2【记忆反证】查 memory / progress / AGENTS，用「已经做过 / 已经是这样」反证排除矛盾解释。\n"
        "3【候选枚举 + 查现状】每个候选读代码 / 看现状，标「现在长啥样、要不要改」。\n"
        "4【web search 业界】对最可能的候选查业界做法。\n"
        "5【收敛，诚实置信度】没把握就老实给 low，不许装懂。\n"
        "6【全程贯穿】不许锁第一个解释。\n\n"
        "遇阻先查证再下结论，不许空想。用户自己也可能表述不准 / 不懂术语——置信度要诚实。\n\n"
        "只输出一个 JSON 对象（不要 markdown 代码块、不要多余文字），结构：\n"
        "{\n"
        '  "recap": "大白话复述：我理解你要做的是 X",\n'
        '  "candidates": ["步骤1枚举的候选1", "候选2"],\n'
        '  "assumptions": [{"text": "假设内容", "has_anchor": true/false}],\n'
        '  "acceptance_criteria": ["可观测的验收标准"],\n'
        '  "confidence": "high | medium | low",\n'
        '  "confidence_reason": "为什么这个置信度"\n'
        "}\n"
        "收口形态由 confidence 决定：high=懂透能验证，直接给执行计划；"
        "medium=推到了差一句话，给「我打算这么做对吗」；"
        "low=连对象都没把握，产镜像「我理解成 X 你看对不对」，不许装懂硬干。\n"
    )


def parse_expansion(cc_output: str) -> ExpansionResult:
    """宽松解析 CC JSON；任何结构问题都降级，且不字符串化类型错误的字段。"""
    try:
        data = json.loads(cc_output)
    except (json.JSONDecodeError, TypeError):
        logger.warning("expansion parse: cc output is not valid JSON; degrading to low")
        return _low(recap="", reason="解析失败：cc 输出不是合法 JSON")
    if not isinstance(data, dict):
        return _low(recap="", reason="解析失败：cc 输出 JSON 顶层不是对象")

    recap_raw = data.get("recap", "")
    recap = recap_raw.strip() if isinstance(recap_raw, str) else ""
    candidates = _to_str_tuple(data.get("candidates"))
    assumptions = _to_assumptions(data.get("assumptions"))
    acceptance_criteria = _to_str_tuple(data.get("acceptance_criteria"))
    confidence = _coerce_confidence(data.get("confidence"))
    reason_raw = data.get("confidence_reason", "")
    reason = (
        reason_raw.strip() if isinstance(reason_raw, str) else ""
    ) or "cc 未给出置信度理由"
    return ExpansionResult(
        recap=recap,
        candidates=candidates,
        assumptions=assumptions,
        acceptance_criteria=acceptance_criteria,
        confidence=confidence,
        confidence_reason=reason,
    )


def expand_todo(todo_text: str, host: CCHost) -> ExpansionResult:
    """通过已注入上下文的 host 执行六步扩展。"""
    prompt = build_expansion_prompt(todo_text)
    raw = host.send(prompt)
    return parse_expansion(raw)


def _low(*, recap: str, reason: str) -> ExpansionResult:
    """空 candidates 明确表示未完成候选枚举，供下游识别降级结果。"""
    return ExpansionResult(
        recap=recap,
        candidates=(),
        assumptions=(),
        acceptance_criteria=(),
        confidence="low",
        confidence_reason=reason,
    )


def _to_str_tuple(value: object) -> tuple[str, ...]:
    """只接收非空字符串项，不能把脏值通过 ``str()`` 伪装成有效内容。"""
    if not isinstance(value, list):
        return ()
    return tuple(
        item.strip() for item in value if isinstance(item, str) and item.strip()
    )


def _to_assumptions(value: object) -> tuple[Assumption, ...]:
    """跳过非法项；缺失 ``has_anchor`` 时按无锚点处理。"""
    if not isinstance(value, list):
        return ()
    out: list[Assumption] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        text_raw = item.get("text", "")
        text = text_raw.strip() if isinstance(text_raw, str) else ""
        if not text:
            continue
        has_anchor = item.get("has_anchor") is True
        out.append(Assumption(text=text, has_anchor=has_anchor))
    return tuple(out)


def _coerce_confidence(value: object) -> Confidence:
    """非法 confidence 一律降级为 ``low``。"""
    if isinstance(value, str) and value.strip() in _VALID_CONFIDENCE:
        return value.strip()  # type: ignore[return-value]
    return "low"
