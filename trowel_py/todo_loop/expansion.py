"""Six-step todo expansion layer — slice-054 命门.

This is the M8 "understanding" core: take a one-line todo, run the six-step
expansion (识别歧义 → 记忆反证 → 候选枚举+查现状 → web search 业界 → 收敛 →
不锁第一个) on a cc session, and parse the reply into a structured
ExpansionResult.

Design (grill 2026-07-16, see docs/slices/activate/slice-054.md「执行编排六步」):
- the CC host is an injected Protocol (``send -> str``); tests mock it and never
  spawn a real ``claude`` (#46416 / test isolation). Production wires the real
  CCHost (cc_host/service.py) later.
- the prompt does NOT re-inject the user profile: trowel's launcher already
  injects profile + memory into cc's system prompt via ``--append-system-prompt``
  (memory/injection.build_memory_injection, slice-039/048). Re-injecting here
  would be redundant. If expand_todo ever runs against a standalone cc WITHOUT
  that injection, the caller must get the profile into cc another way.
- parse is lenient: broken JSON / missing fields / illegal confidence degrade to
  a low-confidence result, never raise (mirrors memory/profile body_to_profile).
  Lenient means bad data does not crash — NOT that bad data is silently str()-coerced
  into plausible-looking dirty entries (a "None" candidate is meaningless).
- confidence is a three-level Literal; anything outside {high,medium,low} → low
  (honest self-assessment, C-2; never pretend to know).

Out of scope (下游未就绪，留桩给后续 slice): 自主执行+自修循环 (slice-055 沙箱),
收口 report 落库 + cc_session_id (slice-056), 调度 (slice-056). This module is
only the understanding layer — the命门 spike-validated as the difference between
李四 (locks first interpretation) and 张三 (enumerates, 反证, converges honestly).
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
    """One explicit assumption, tagged by whether it has a code anchor.

    Attributes:
        text: the assumption stated in plain words.
        has_anchor: True only if backed by a real code/memory anchor; False for
            pure guesses. Uses ``is True`` so only a genuine boolean True counts
            (``"false"`` / ``1`` from a malformed reply do not sneak in as True).
    """

    text: str
    has_anchor: bool


@dataclass(frozen=True)
class ExpansionResult:
    """Structured output of the six-step expansion (命门 layer).

    Attributes:
        recap: plain-word restatement "我理解你要做的是 X".
        candidates: the指代 candidates enumerated in step 1 (non-empty = did NOT
            lock the first interpretation, C-1). Empty signals a violation.
        assumptions: explicit assumptions, each tagged has_anchor.
        acceptance_criteria: observable, checkable done-conditions.
        confidence: high (无人执行) / medium (待一句话) / low (产镜像).
        confidence_reason: why this confidence — ambiguity, anchor sufficiency.
    """

    recap: str
    candidates: tuple[str, ...]
    assumptions: tuple[Assumption, ...]
    acceptance_criteria: tuple[str, ...]
    confidence: Confidence
    confidence_reason: str


class CCHost(Protocol):
    """Minimal contract for something that sends a prompt and returns cc's reply."""

    def send(self, message: str) -> str: ...


def build_expansion_prompt(todo_text: str) -> str:
    """Build the six-step expansion prompt for cc.

    Relies on the cc session ALREADY carrying the user profile + memory in its
    system prompt — trowel's launcher injects them via ``--append-system-prompt``
    (memory/injection.build_memory_injection, slice-039/048). So this prompt does
    NOT re-inject the profile; it adds only the six-step flow + the output shape.

    Args:
        todo_text: the user's one-line todo, verbatim.

    Returns:
        the full prompt to send to cc.
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
    """Parse cc's JSON reply into an ExpansionResult (lenient, never raises).

    Broken JSON, a non-object top level, missing fields, or an illegal confidence
    all degrade to a low-confidence result rather than raising — a bad expansion
    must not crash the todo loop. An illegal confidence (e.g. "very high", None,
    an int) is coerced to "low" (honest self-assessment, C-2). Non-string values
    where strings are expected are skipped/defaulted, never str()-coerced.

    Args:
        cc_output: the raw text cc returned.

    Returns:
        an ExpansionResult; on any parse trouble, a low-confidence one whose
        confidence_reason explains the failure.
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
    reason = (reason_raw.strip() if isinstance(reason_raw, str) else "") or "cc 未给出置信度理由"
    return ExpansionResult(
        recap=recap,
        candidates=candidates,
        assumptions=assumptions,
        acceptance_criteria=acceptance_criteria,
        confidence=confidence,
        confidence_reason=reason,
    )


def expand_todo(todo_text: str, host: CCHost) -> ExpansionResult:
    """Run the six-step expansion on one todo: build prompt → send to cc → parse.

    The cc session is expected to already carry the user profile + memory in its
    system prompt (trowel launcher injection, slice-039/048), so no profile is
    passed here. If the host is a standalone cc WITHOUT that injection, the
    caller must get the profile into cc another way.

    Args:
        todo_text: the user's one-line todo, verbatim.
        host: a CCHost (Protocol) whose send returns cc's reply. Tests inject a
            fake; production wires the real CCHost (cc_host/service.py).

    Returns:
        the parsed ExpansionResult.
    """
    prompt = build_expansion_prompt(todo_text)
    raw = host.send(prompt)
    return parse_expansion(raw)


def _low(*, recap: str, reason: str) -> ExpansionResult:
    """Build a low-confidence fallback result (used on parse trouble).

    candidates is left empty — an empty candidates tuple signals a C-1 violation
    (locked first interpretation) that callers can detect downstream.
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
    """Coerce a JSON list into a tuple of non-empty stripped strings.

    Non-string items are SKIPPED, not str()-coerced: lenient means bad data does
    not crash, not that bad data silently becomes a plausible-looking dirty entry
    (str(None) -> "None" as a candidate would be meaningless to a human reader).
    """
    if not isinstance(value, list):
        return ()
    return tuple(
        item.strip() for item in value if isinstance(item, str) and item.strip()
    )


def _to_assumptions(value: object) -> tuple[Assumption, ...]:
    """Coerce a JSON list of {text, has_anchor} into Assumptions (lenient).

    A non-dict item is skipped; an empty text is skipped; a missing has_anchor
    defaults to False (pure guess). has_anchor uses ``is True`` so only a genuine
    boolean True counts (a malformed ``"false"`` string is not treated as True).
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
    """Coerce a confidence value to {high,medium,low}; anything else → low."""
    if isinstance(value, str) and value.strip() in _VALID_CONFIDENCE:
        return value.strip()  # type: ignore[return-value]
    return "low"
