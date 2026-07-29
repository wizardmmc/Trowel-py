"""调用 Claude Code 按六步流程理解 todo 文本，并将回复解析为结构化结果。

调用方必须提供已注入 profile 和 memory 的 host。回复不是合法 JSON 对象时，
返回不含复述、候选、假设和验收标准的低置信度结果。对象内缺失或类型错误的字段
采用空值、默认理由或 ``low``，不会通过 ``str()`` 强制转成文本。
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
    """记录 Claude Code 为理解 todo 而作出的假设。

    Attributes:
        text: 假设的具体内容。
        has_anchor: 是否能在代码或 memory 中找到支持这项假设的依据；只有布尔值
            ``True`` 表示有依据。
    """

    text: str
    has_anchor: bool


@dataclass(frozen=True)
class ExpansionResult:
    """记录 Claude Code 对 todo 的理解结果。

    Attributes:
        recap: 用大白话复述的 todo 意图。
        candidates: todo 可能指向的改动对象或解释。
        assumptions: 理解 todo 时采用的显式假设。
        acceptance_criteria: 用于确认 todo 已完成的可观察标准。
        confidence: 对当前理解的把握程度，取 ``high``、``medium`` 或 ``low``。
        confidence_reason: 采用当前把握程度的原因。
    """

    recap: str
    candidates: tuple[str, ...]
    assumptions: tuple[Assumption, ...]
    acceptance_criteria: tuple[str, ...]
    confidence: Confidence
    confidence_reason: str


class CCHost(Protocol):
    """定义 todo 扩展使用的 Claude Code 消息接口。"""

    def send(self, message: str) -> str:
        """将提示词交给 Claude Code 并返回原始回复。

        Args:
            message: 已组装的六步理解提示词。

        Returns:
            Claude Code 返回的完整文本。
        """
        ...


def build_expansion_prompt(todo_text: str) -> str:
    """生成要求 Claude Code 分六步理解 todo 的提示词。

    提示词直接使用 Claude Code system prompt 中已有的 profile 和 memory，不会
    重复注入这些上下文。

    Args:
        todo_text: 用户记录的 todo 原文。
    """
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
    """将 Claude Code 的 JSON 回复解析为结构化结果。

    无法解析 JSON 或顶层不是对象时，返回低置信度的空结果；其余非法字段分别
    采用安全默认值。

    Args:
        cc_output: Claude Code 返回的原始文本。
    """
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
    """让 Claude Code 按六步流程理解 todo，并解析其回复。

    Args:
        todo_text: 用户记录的 todo 原文。
        host: 消息接口，其 system prompt 已包含用户的 profile 和 memory。
    """
    prompt = build_expansion_prompt(todo_text)
    raw = host.send(prompt)
    return parse_expansion(raw)


def _low(*, recap: str, reason: str) -> ExpansionResult:
    """生成不含候选解释的低置信度结果。

    空 candidates 明确表示未完成候选枚举，供下游识别降级结果。

    Args:
        recap: 已成功取得的 todo 意图复述；没有可用复述时为空字符串。
        reason: 将结果降为低置信度的原因。
    """
    return ExpansionResult(
        recap=recap,
        candidates=(),
        assumptions=(),
        acceptance_criteria=(),
        confidence="low",
        confidence_reason=reason,
    )


def _to_str_tuple(value: object) -> tuple[str, ...]:
    """从列表中提取去除首尾空白后的非空字符串。

    其他类型的列表项会被丢弃，不会通过 ``str()`` 强制转换。

    Args:
        value: Claude Code 回复中的候选解释或验收标准字段。
    """
    if not isinstance(value, list):
        return ()
    return tuple(
        item.strip() for item in value if isinstance(item, str) and item.strip()
    )


def _to_assumptions(value: object) -> tuple[Assumption, ...]:
    """从列表中提取含有非空文本的假设。

    非对象项和没有有效文本的对象会被丢弃；``has_anchor`` 只有等于布尔值
    ``True`` 时才表示存在依据。

    Args:
        value: Claude Code 回复中的假设字段。
    """
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
    """将 confidence 字段规范为 ``high``、``medium`` 或 ``low``。

    Args:
        value: Claude Code 回复中的 confidence 字段；无法识别时按 ``low`` 处理。
    """
    if isinstance(value, str) and value.strip() in _VALID_CONFIDENCE:
        return value.strip()  # type: ignore[return-value]
    return "low"
