"""将 Daily 来源转换为模型输入，并校验、去重和筛选模型生成的摘要条目。"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from dataclasses import dataclass
from typing import Any

from trowel_py.llm.client import LLMProvider
from trowel_py.memory.prompt import DAILY_ITEM_TYPES, build_daily_compress_prompt

logger = logging.getLogger("trowel_py.memory.compress")

_DAILY_BUDGET = 800
_DAILY_GENERATION_VERSION = 2

_SECTION_FOR_TYPE: dict[str, str] = {
    "outcome": "进展",
    "decision": "进展",
    "correction": "更正",
    "open_loop": "待续",
}
_SECTION_ORDER = ("进展", "更正", "待续")
_TYPE_PRIORITY: dict[str, int] = {
    "correction": 1,
    "open_loop": 1,
    "outcome": 0,
    "decision": 0,
}


@dataclass(frozen=True)
class _DailyItem:
    """记录一条已通过来源校验、可参与 Daily 渲染的摘要。

    Attributes:
        type: 条目类别，为 ``outcome``、``decision``、``correction`` 或
            ``open_loop``。
        text: 要写入 Daily 的摘要正文。
        source: 条目所依据的真实 Episode segment ID。
    """

    type: str
    text: str
    source: str


def _source_hash(sources: list[tuple[str, str, Any]]) -> str:
    """按来源顺序哈希 segment ID、结构化条目和非空 legacy events，返回 SHA-256 前 16 个十六进制字符。"""
    parts: list[str] = []
    for seg_id, _registered_at, entry in sources:
        parts.append(seg_id)
        for field in ("outcomes", "decisions", "corrections", "open_loops"):
            parts.append(field)
            parts.extend(getattr(entry, field))
        if entry.events.strip():
            parts.append("events:" + entry.events.strip())
    blob = "\n".join(parts)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def _source_aliases(
    sources: list[tuple[str, str, Any]],
) -> dict[str, str]:
    """按来源顺序将 ``S1``、``S2`` 等短别名映射到真实 segment ID。"""
    return {
        f"S{index}": seg_id
        for index, (seg_id, _registered_at, _entry) in enumerate(sources, 1)
    }


def _required_sections(sources: list[tuple[str, str, Any]]) -> set[str]:
    """返回模型摘要必须覆盖的 Daily section。"""
    required: set[str] = set()
    for _seg_id, _registered_at, entry in sources:
        if entry.outcomes or entry.decisions:
            required.add("进展")
        if entry.corrections:
            required.add("更正")
        if entry.open_loops:
            required.add("待续")
    return required


def _render_sources_block(
    sources: list[tuple[str, str, Any]], aliases: dict[str, str]
) -> str:
    """将来源渲染为模型输入，并用短别名代替较长的 segment ID。"""
    lines: list[str] = []
    alias_for = {real_id: alias for alias, real_id in aliases.items()}
    for seg_id, _registered_at, entry in sources:
        lines.append(f"【segment {alias_for[seg_id]}】")
        wrote = False
        for field in ("outcomes", "decisions", "corrections", "open_loops"):
            items = getattr(entry, field)
            if items:
                lines.append(f"{field}:")
                lines.extend(f"- {it}" for it in items)
                wrote = True
        if entry.events.strip():
            lines.append("events (legacy 自由文本，从中提取结构化 items):")
            lines.append(entry.events.strip())
            wrote = True
        if not wrote:
            lines.append("(该 segment 无结构化经历)")
        lines.append("")
    return "\n".join(lines).strip()


def _parse_and_validate(
    raw: str,
    source_aliases: dict[str, str],
    required_sections: set[str],
) -> tuple[list[_DailyItem], list[str]]:
    """解析模型响应，并校验每个条目的类别、正文、来源和 section 覆盖。

    响应可以在第一个 ``{`` 前以及解析出的 JSON 对象后包含其他文本。来源既可
    使用当前 prompt 提供的短别名，也可使用旧版模型输出中的完整 segment ID。

    Args:
        raw: 模型返回的原始文本。
        source_aliases: 短别名到真实 Episode segment ID 的映射。
        required_sections: 本次来源要求模型覆盖的 Daily section。

    Returns:
        已通过逐条校验的摘要及全部校验错误。错误列表非空时，调用方会拒绝整次响应。
    """
    errors: list[str] = []
    start = raw.find("{")
    if start < 0:
        return [], ["response has no JSON object"]
    try:
        data, _end = json.JSONDecoder().raw_decode(raw[start:])
    except json.JSONDecodeError as exc:
        return [], [f"invalid JSON: {exc}"]
    raw_items = data.get("items")
    if not isinstance(raw_items, list):
        return [], ["'items' missing or not a list"]
    valid_ids = set(source_aliases.values())
    items: list[_DailyItem] = []
    for i, it in enumerate(raw_items):
        if not isinstance(it, dict):
            errors.append(f"items[{i}]: not an object")
            continue
        typ = str(it.get("type", "")).strip()
        text = str(it.get("text", "")).strip()
        source = str(it.get("source", "")).strip()
        if typ not in DAILY_ITEM_TYPES:
            errors.append(f"items[{i}]: bad type {typ!r}")
            continue
        if not text:
            errors.append(f"items[{i}]: empty text")
            continue
        resolved_source = source_aliases.get(source)
        # 兼容旧版模型输出中的完整 segment ID；当前 prompt 只提供短别名。
        if resolved_source is None and source in valid_ids:
            resolved_source = source
        if resolved_source is None:
            errors.append(
                f"items[{i}]: source {source!r} not in provided aliases "
                f"{list(source_aliases)}"
            )
            continue
        items.append(_DailyItem(type=typ, text=text, source=resolved_source))
    present_sections = {
        section
        for item in items
        if (section := _SECTION_FOR_TYPE.get(item.type)) is not None
    }
    for section in _SECTION_ORDER:
        if section in required_sections and section not in present_sections:
            errors.append(f"missing required section {section!r}")
    return items, errors


def _generate_items(
    provider: LLMProvider,
    date_str: str,
    sources_block: str,
    source_aliases: dict[str, str],
    required_sections: set[str],
) -> tuple[list[_DailyItem], str]:
    """调用模型生成 Daily 条目，首次输出不合格时附上错误重试一次。

    Args:
        provider: 提供文本补全的模型客户端。
        date_str: 摘要日期，格式为 ``YYYY-MM-DD``。
        sources_block: 已渲染的 Episode 来源文本。
        source_aliases: 短别名到真实 Episode segment ID 的映射。
        required_sections: 模型输出必须覆盖的 Daily section。

    Returns:
        首次调用成功且响应通过校验时返回条目及 ``"ok"``；首次响应校验失败时
        重试一次。任一次模型调用抛出异常，或重试响应仍未通过校验时，返回空列表
        及 ``"fallback"``。
    """
    sys_prompt = (
        "你是日记压缩器。把当天结构化经历压缩成可回忆的当天摘要，输出带 source 的结构化 items。"
        "只输出 JSON 对象，不要 markdown，不要解释。"
    )
    try:
        raw1 = provider.complete(
            sys_prompt,
            build_daily_compress_prompt(date=date_str, sources_block=sources_block),
        )
    except Exception:  # noqa: BLE001
        logger.warning("daily %s: provider call failed", date_str, exc_info=True)
        return [], "fallback"
    items1, errs1 = _parse_and_validate(raw1, source_aliases, required_sections)
    if not errs1:
        return items1, "ok"
    logger.info("daily %s: first response rejected (%s) — retrying", date_str, errs1[:3])
    try:
        retry_prompt = (
            build_daily_compress_prompt(date=date_str, sources_block=sources_block)
            + "\n\n【上次返回被拒绝，具体错误】\n- "
            + "\n- ".join(errs1)
        )
        raw2 = provider.complete(sys_prompt, retry_prompt)
    except Exception:  # noqa: BLE001
        logger.warning("daily %s: provider retry failed", date_str, exc_info=True)
        return [], "fallback"
    items2, errs2 = _parse_and_validate(raw2, source_aliases, required_sections)
    if errs2:
        logger.warning("daily %s: retry still rejected (%s) — fallback", date_str, errs2[:3])
        return [], "fallback"
    return items2, "ok"


def _normalize(text: str) -> str:
    """删除所有空白，生成 Daily 条目的精确去重键。"""
    return re.sub(r"\s+", "", text).strip()


def _dedup_items(items: list[_DailyItem]) -> list[_DailyItem]:
    """按去除空白后的正文精确去重，并保留首次出现的条目。"""
    seen: set[str] = set()
    out: list[_DailyItem] = []
    for it in items:
        key = _normalize(it.text)
        if key and key not in seen:
            seen.add(key)
            out.append(it)
    return out


def _render_daily_body(date_str: str, items: list[_DailyItem]) -> str:
    """按进展、更正、待续的顺序将非空 section 渲染为 Markdown。"""
    by_section: dict[str, list[str]] = {s: [] for s in _SECTION_ORDER}
    for it in items:
        section = _SECTION_FOR_TYPE.get(it.type)
        if section:
            by_section[section].append(it.text)
    parts = [f"# {date_str}"]
    for section in _SECTION_ORDER:
        bullets = by_section[section]
        if bullets:
            parts.append(f"## {section}\n" + "\n".join(f"- {b}" for b in bullets))
    return "\n\n".join(parts) + "\n"


def _select_within_budget(
    date_str: str, items: list[_DailyItem], budget: int = _DAILY_BUDGET
) -> list[_DailyItem]:
    """按完整条目删减到字符预算，同时为每个已有 section 保留至少一项。

    优先删除结果和决定，再删除更正和待续。如果保底条目仍然超出预算，返回值也会
    超出预算，由调用方改写为 fallback。

    Args:
        date_str: 用于渲染长度的 Daily 日期，格式为 ``YYYY-MM-DD``。
        items: 已通过校验并去重的摘要条目。
        budget: Daily Markdown 正文允许的最大字符数。

    Returns:
        保持原有相对顺序的筛选结果。
    """
    selected = list(items)
    while len(selected) > 1 and len(_render_daily_body(date_str, selected)) > budget:
        section_counts = {
            section: sum(
                _SECTION_FOR_TYPE.get(item.type) == section for item in selected
            )
            for section in _SECTION_ORDER
        }
        removable = [
            i
            for i, it in enumerate(selected)
            if section_counts.get(_SECTION_FOR_TYPE.get(it.type, ""), 0) > 1
        ]
        if not removable:
            break
        low_priority = [
            i for i in removable if _TYPE_PRIORITY.get(selected[i].type, 0) == 0
        ]
        drop = low_priority[-1] if low_priority else removable[-1]
        selected.pop(drop)
    return selected
