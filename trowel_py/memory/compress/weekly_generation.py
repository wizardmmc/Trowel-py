"""Weekly v3 的结构化生成、来源校验与预算选择。"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass

from trowel_py.llm.client import LLMProvider

logger = logging.getLogger("trowel_py.memory.compress")

WEEKLY_BUDGET = 800
WEEKLY_GENERATION_VERSION = 3
WEEKLY_PROMPT_BUDGET = 8000
BYPASS_BUDGET = 800
BYPASS_CATEGORIES = (
    "technical-detail",
    "emotional-trigger",
    "cross-week-causal",
)

_ITEM_TYPES = {"outcome", "decision", "correction", "open_loop"}
_SECTION_FOR_TYPE = {
    "outcome": "进展",
    "decision": "进展",
    "correction": "更正",
    "open_loop": "待续",
}
_SECTION_ORDER = ("进展", "更正", "待续")
_SECONDARY_TYPES = {"correction", "open_loop"}
_SYSTEM_PROMPT = (
    "你是周记压缩器。weekly 比 daily 更模糊，只需记住本周做了什么。"
    "把同一工作主线跨日合并为少量周级事项，技术细节放入 bypass，不要逐日复述。"
    "主 items 优先保留 outcome 和 decision；correction、open_loop 只留最重要的。"
    "items.type 只能是 outcome、decision、correction、open_loop；每项必须给出非空 source_days，"
    "且只能引用输入中的日期；items.source_days 的并集必须覆盖每个输入日期。"
    "主 items 必须能在 800 字以内完整呈现；必要时合并跨日事项、缩短到周级粒度。"
    "bypass 只能包含 technical-detail、emotional-trigger、"
    "cross-week-causal 三类，每条同样带 source_days。保持模型给出的重要性顺序。"
    "只输出 JSON 对象，不要 markdown，不要解释。"
)


@dataclass(frozen=True)
class WeeklySource:
    day: str
    body: str


@dataclass(frozen=True)
class WeeklyItem:
    type: str
    text: str
    source_days: tuple[str, ...]


@dataclass(frozen=True)
class WeeklyBypassItem:
    text: str
    source_days: tuple[str, ...]


@dataclass(frozen=True)
class WeeklyGeneration:
    items: tuple[WeeklyItem, ...]
    bypass: dict[str, tuple[WeeklyBypassItem, ...]]


def required_sections(sources: list[WeeklySource]) -> set[str]:
    required: set[str] = set()
    for source in sources:
        for section in _SECTION_ORDER:
            if re.search(rf"^## {re.escape(section)}\s*$", source.body, re.MULTILINE):
                required.add(section)
    return required


def _render_source(source: WeeklySource) -> str:
    return f"## source_day: {source.day}\n{source.body.strip()}\n"


def _source_chunks(sources: list[WeeklySource]) -> list[list[WeeklySource]]:
    chunks: list[list[WeeklySource]] = []
    current: list[WeeklySource] = []
    current_size = 0
    for source in sources:
        rendered_size = len(_render_source(source)) + 2
        if current and current_size + rendered_size > WEEKLY_PROMPT_BUDGET:
            chunks.append(current)
            current = []
            current_size = 0
        current.append(source)
        current_size += rendered_size
    if current:
        chunks.append(current)
    return chunks


def _user_prompt(sources: list[WeeklySource]) -> str:
    rendered = "\n".join(_render_source(source) for source in sources)
    return (
        "本批 daily（每个 source_day 是唯一合法来源）：\n"
        f"{rendered}\n"
        "输出 JSON："
        '{"items":[{"type":"outcome","text":"完整句",'
        '"source_days":["YYYY-MM-DD"]}],"bypass":{'
        '"technical-detail":[{"text":"完整句","source_days":["YYYY-MM-DD"]}],'
        '"emotional-trigger":[],"cross-week-causal":[]}}'
    )


def _source_days(
    value: object,
    *,
    legal_days: set[str],
    field: str,
    errors: list[str],
) -> tuple[str, ...] | None:
    if not isinstance(value, list) or not value:
        errors.append(f"{field}: source_days must be a non-empty list")
        return None
    days = tuple(str(day).strip() for day in value)
    if any(not day for day in days):
        errors.append(f"{field}: source_days contains an empty day")
        return None
    if len(set(days)) != len(days):
        errors.append(f"{field}: source_days contains duplicates")
        return None
    illegal = [day for day in days if day not in legal_days]
    if illegal:
        errors.append(f"{field}: illegal source_days {illegal}")
        return None
    return days


def parse_and_validate(
    raw: str,
    legal_days: set[str],
    required: set[str],
) -> tuple[WeeklyGeneration | None, list[str]]:
    errors: list[str] = []
    start = raw.find("{")
    if start < 0:
        return None, ["response has no JSON object"]
    try:
        data, _end = json.JSONDecoder().raw_decode(raw[start:])
    except json.JSONDecodeError as exc:
        return None, [f"invalid JSON: {exc}"]
    if not isinstance(data, dict):
        return None, ["response JSON must be an object"]

    raw_items = data.get("items")
    if not isinstance(raw_items, list):
        return None, ["'items' missing or not a list"]
    items: list[WeeklyItem] = []
    for index, value in enumerate(raw_items):
        field = f"items[{index}]"
        if not isinstance(value, dict):
            errors.append(f"{field}: not an object")
            continue
        item_type = str(value.get("type") or "").strip()
        text = str(value.get("text") or "").strip()
        days = _source_days(
            value.get("source_days"),
            legal_days=legal_days,
            field=field,
            errors=errors,
        )
        if item_type not in _ITEM_TYPES:
            errors.append(f"{field}: bad type {item_type!r}")
        if not text:
            errors.append(f"{field}: empty text")
        if item_type in _ITEM_TYPES and text and days is not None:
            items.append(WeeklyItem(item_type, text, days))

    present = {_SECTION_FOR_TYPE[item.type] for item in items}
    for section in _SECTION_ORDER:
        if section in required and section not in present:
            errors.append(f"missing required section {section!r}")
    covered_days = {day for item in items for day in item.source_days}
    missing_days = sorted(legal_days - covered_days)
    if missing_days:
        errors.append(f"items missing source day coverage: {missing_days!r}")

    raw_bypass = data.get("bypass") or {}
    bypass: dict[str, tuple[WeeklyBypassItem, ...]] = {}
    if not isinstance(raw_bypass, dict):
        errors.append("'bypass' must be an object")
        raw_bypass = {}
    unknown = set(raw_bypass) - set(BYPASS_CATEGORIES)
    if unknown:
        errors.append(f"unknown bypass categories {sorted(unknown)}")
    for category in BYPASS_CATEGORIES:
        raw_category = raw_bypass.get(category) or []
        if not isinstance(raw_category, list):
            errors.append(f"bypass.{category}: must be a list")
            continue
        parsed_category: list[WeeklyBypassItem] = []
        for index, value in enumerate(raw_category):
            field = f"bypass.{category}[{index}]"
            if not isinstance(value, dict):
                errors.append(f"{field}: not an object")
                continue
            text = str(value.get("text") or "").strip()
            days = _source_days(
                value.get("source_days"),
                legal_days=legal_days,
                field=field,
                errors=errors,
            )
            if not text:
                errors.append(f"{field}: empty text")
            if text and days is not None:
                parsed_category.append(WeeklyBypassItem(text, days))
        bypass[category] = tuple(parsed_category)
    if errors:
        return None, errors
    return WeeklyGeneration(tuple(items), bypass), []


def _generate_chunk(
    provider: LLMProvider,
    sources: list[WeeklySource],
) -> WeeklyGeneration | None:
    prompt = _user_prompt(sources)
    legal_days = {source.day for source in sources}
    required = required_sections(sources)
    errors: list[str] = []
    for attempt in range(2):
        retry = ""
        if errors:
            retry = "\n\n【上次返回被拒绝，具体错误】\n- " + "\n- ".join(errors)
        try:
            raw = provider.complete(_SYSTEM_PROMPT, prompt + retry)
        except Exception:  # noqa: BLE001
            logger.warning("weekly provider call failed", exc_info=True)
            return None
        generation, errors = parse_and_validate(raw, legal_days, required)
        if not errors:
            assert generation is not None
            selected = select_weekly_items(
                "0000-W00",
                generation.items,
                required_days=legal_days,
            )
            selected_body = render_weekly("0000-W00", selected)
            if len(selected_body) <= WEEKLY_BUDGET:
                return generation
            errors = [
                f"完整 section/day 覆盖仍有 {len(selected_body)} 字，超过"
                f" {WEEKLY_BUDGET} 字；请合并跨日事项并缩短到周级粒度"
            ]
        if attempt == 0:
            logger.info("weekly response rejected (%s); retrying", errors[:3])
    logger.warning("weekly retry still rejected (%s)", errors[:3])
    return None


def _compact_items(
    provider: LLMProvider,
    items: list[WeeklyItem],
    *,
    legal_days: set[str],
    required: set[str],
) -> tuple[WeeklyItem, ...] | None:
    candidates = json.dumps(
        [weekly_item_to_dict(item) for item in items],
        ensure_ascii=False,
    )
    prompt = (
        "以下是分批生成后仍然过长的 weekly 主候选。请合并同一工作主线并缩短到"
        f" {WEEKLY_BUDGET} 字以内，同时保留全部日期和必要 section。"
        "只返回与原契约相同的 JSON，bypass 可为空。\n"
        f"分批候选：{candidates}"
    )
    errors: list[str] = []
    for attempt in range(2):
        retry = ""
        if errors:
            retry = "\n\n【上次返回被拒绝，具体错误】\n- " + "\n- ".join(errors)
        try:
            raw = provider.complete(_SYSTEM_PROMPT, prompt + retry)
        except Exception:  # noqa: BLE001
            logger.warning("weekly candidate compaction failed", exc_info=True)
            return None
        generation, errors = parse_and_validate(raw, legal_days, required)
        if not errors:
            assert generation is not None
            selected = select_weekly_items(
                "0000-W00",
                generation.items,
                required_days=legal_days,
            )
            selected_body = render_weekly("0000-W00", selected)
            if len(selected_body) <= WEEKLY_BUDGET:
                return generation.items
            errors = [
                f"完整 section/day 覆盖仍有 {len(selected_body)} 字，超过"
                f" {WEEKLY_BUDGET} 字；请继续合并和缩短"
            ]
        if attempt == 0:
            logger.info("weekly compacted candidates rejected (%s); retrying", errors[:3])
    logger.warning("weekly candidate compaction retry still rejected (%s)", errors[:3])
    return None


def _dedup_items(items: list[WeeklyItem]) -> list[WeeklyItem]:
    positions: dict[tuple[str, str], int] = {}
    out: list[WeeklyItem] = []
    for item in items:
        normalized = re.sub(r"\s+", "", item.text)
        if not normalized:
            continue
        key = (item.type, normalized)
        if key not in positions:
            positions[key] = len(out)
            out.append(item)
            continue
        existing_index = positions[key]
        existing = out[existing_index]
        merged_days = tuple(dict.fromkeys(existing.source_days + item.source_days))
        out[existing_index] = WeeklyItem(existing.type, existing.text, merged_days)
    return out


def generate_weekly(
    provider: LLMProvider,
    sources: list[WeeklySource],
) -> WeeklyGeneration | None:
    items: list[WeeklyItem] = []
    bypass: dict[str, list[WeeklyBypassItem]] = {
        category: [] for category in BYPASS_CATEGORIES
    }
    for chunk in _source_chunks(sources):
        generation = _generate_chunk(provider, chunk)
        if generation is None:
            return None
        items.extend(generation.items)
        for category in BYPASS_CATEGORIES:
            bypass[category].extend(generation.bypass.get(category, ()))
    deduped = _dedup_items(items)
    legal_days = {source.day for source in sources}
    selected = select_weekly_items(
        "0000-W00",
        tuple(deduped),
        required_days=legal_days,
    )
    if len(render_weekly("0000-W00", selected)) > WEEKLY_BUDGET:
        compacted = _compact_items(
            provider,
            deduped,
            legal_days=legal_days,
            required=required_sections(sources),
        )
        if compacted is None:
            return None
        deduped = _dedup_items(list(compacted))
    return WeeklyGeneration(
        items=tuple(deduped),
        bypass={category: tuple(values) for category, values in bypass.items()},
    )


def render_weekly(period: str, items: list[WeeklyItem]) -> str:
    by_section: dict[str, list[str]] = {section: [] for section in _SECTION_ORDER}
    for item in items:
        by_section[_SECTION_FOR_TYPE[item.type]].append(item.text)
    parts = [f"# {period}"]
    for section in _SECTION_ORDER:
        bullets = by_section[section]
        if bullets:
            parts.append(f"## {section}\n" + "\n".join(f"- {text}" for text in bullets))
    return "\n\n".join(parts) + "\n"


def select_weekly_items(
    period: str,
    items: tuple[WeeklyItem, ...],
    budget: int = WEEKLY_BUDGET,
    *,
    required_days: set[str] | None = None,
) -> list[WeeklyItem]:
    selected = list(items)
    protected_days = required_days or {
        day for item in selected for day in item.source_days
    }
    while len(selected) > 1 and len(render_weekly(period, selected)) > budget:
        counts = {
            section: sum(
                _SECTION_FOR_TYPE[item.type] == section for item in selected
            )
            for section in _SECTION_ORDER
        }
        day_counts = {
            day: sum(day in item.source_days for item in selected)
            for day in protected_days
        }
        removable = [
            index
            for index, item in enumerate(selected)
            if counts[_SECTION_FOR_TYPE[item.type]] > 1
            and all(
                day not in protected_days or day_counts[day] > 1
                for day in item.source_days
            )
        ]
        if not removable:
            break
        secondary = [
            index for index in removable if selected[index].type in _SECONDARY_TYPES
        ]
        selected.pop(secondary[-1] if secondary else removable[-1])
    return selected


def select_bypass_items(
    items: tuple[WeeklyBypassItem, ...],
    budget: int = BYPASS_BUDGET,
) -> list[WeeklyBypassItem]:
    selected = list(items)
    while selected and len(render_bypass(selected)) > budget:
        selected.pop()
    return selected


def render_bypass(items: list[WeeklyBypassItem]) -> str:
    return "\n".join(f"- {item.text}" for item in items) + ("\n" if items else "")


def weekly_item_to_dict(item: WeeklyItem) -> dict[str, object]:
    return {
        "type": item.type,
        "text": item.text,
        "source_days": list(item.source_days),
    }


def bypass_item_to_dict(item: WeeklyBypassItem) -> dict[str, object]:
    return {"text": item.text, "source_days": list(item.source_days)}
