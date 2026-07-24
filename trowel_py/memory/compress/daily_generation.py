"""Daily 摘要的来源投影、模型校验与预算选择。"""

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
    """经过来源校验的一条 daily 摘要。"""

    type: str
    text: str
    source: str


def _source_hash(sources: list[tuple[str, str, Any]]) -> str:
    """计算全部结构化来源的稳定摘要。"""
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
    """按来源顺序把短别名映射到真实 segment id。"""
    return {
        f"S{index}": seg_id
        for index, (seg_id, _registered_at, _entry) in enumerate(sources, 1)
    }


def _required_sections(sources: list[tuple[str, str, Any]]) -> set[str]:
    """根据结构化来源确定摘要不得遗漏的 section。"""
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
    """使用便于模型复制的短别名渲染结构化来源。"""
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
    """解析模型响应并校验类型、正文、来源和必需 section。"""
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
        # 兼容旧 provider 返回的完整 id，新 prompt 只暴露短别名。
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
    """调用模型并在首次验证失败时携带错误重试一次。"""
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
    return re.sub(r"\s+", "", text).strip()


def _dedup_items(items: list[_DailyItem]) -> list[_DailyItem]:
    """按规范化正文精确去重，保留首次出现项。"""
    seen: set[str] = set()
    out: list[_DailyItem] = []
    for it in items:
        key = _normalize(it.text)
        if key and key not in seen:
            seen.add(key)
            out.append(it)
    return out


def _render_daily_body(date_str: str, items: list[_DailyItem]) -> str:
    """按固定顺序渲染非空 section。"""
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
    """整条淘汰低优先项，同时为每个已有 section 至少保留一项。"""
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
