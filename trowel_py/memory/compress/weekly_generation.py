"""生成并校验结构化 Weekly，按完整条目控制主摘要与 bypass 预算。"""

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
    """保存一份参与 Weekly 生成的 Daily。

    Attributes:
        day: Daily 对应的 ISO 日期，也是模型可以引用的来源标识。
        body: 发送给模型的完整 Daily 正文。
    """

    day: str
    body: str


@dataclass(frozen=True)
class WeeklyItem:
    """保存一条可追溯到 Daily 日期的 Weekly 主摘要。

    Attributes:
        type: 条目类型；生成结果只允许 outcome、decision、correction 或 open_loop。
        text: 写入 Weekly 正文的完整条目文本。
        source_days: 支持该条目的 Daily 日期，顺序沿用模型输出。
    """

    type: str
    text: str
    source_days: tuple[str, ...]


@dataclass(frozen=True)
class WeeklyBypassItem:
    """保存一条不进入 Weekly 主摘要的细节。

    Attributes:
        text: 写入 bypass 文件的完整条目文本。
        source_days: 支持该条目的 Daily 日期，顺序沿用模型输出。
    """

    text: str
    source_days: tuple[str, ...]


@dataclass(frozen=True)
class WeeklyGeneration:
    """保存一次通过结构校验的 Weekly 模型结果。

    Attributes:
        items: Weekly 主摘要候选。
        bypass: 三个固定类别各自保留的旁路条目；缺少的类别以空元组表示。
    """

    items: tuple[WeeklyItem, ...]
    bypass: dict[str, tuple[WeeklyBypassItem, ...]]


def required_sections(sources: list[WeeklySource]) -> set[str]:
    """找出 Daily 中出现过且 Weekly 必须保留的分区。

    Args:
        sources: 本次 Weekly 使用的全部 Daily。

    Returns:
        正文中以二级标题出现的“进展”“更正”和“待续”分区集合。
    """
    required: set[str] = set()
    for source in sources:
        for section in _SECTION_ORDER:
            if re.search(rf"^## {re.escape(section)}\s*$", source.body, re.MULTILINE):
                required.add(section)
    return required


def _render_source(source: WeeklySource) -> str:
    """把 Daily 正文连同可引用的来源日期渲染为模型输入。

    Args:
        source: 要发送给模型的 Daily。

    Returns:
        以 ``source_day`` 标题开头并去除正文首尾空白的文本。
    """
    return f"## source_day: {source.day}\n{source.body.strip()}\n"


def _source_chunks(sources: list[WeeklySource]) -> list[list[WeeklySource]]:
    """按提示预算把 Daily 分成保持原顺序的完整批次。

    单份 Daily 即使超过预算也不会被拆分或丢弃。

    Args:
        sources: 按日期排列的 Daily 来源。

    Returns:
        保持输入顺序的非空批次；没有来源时为空。
    """
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
    """为一批 Daily 构造结构化 Weekly 生成提示。

    Args:
        sources: 当前批次的完整 Daily 来源。

    Returns:
        包含合法来源日期、完整正文和 JSON 输出格式的用户提示。
    """
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
    """校验模型条目中的来源日期，并把错误追加到共享列表。

    Args:
        value: 模型返回的 ``source_days`` 字段。
        legal_days: 当前批次允许引用的 Daily 日期。
        field: 条目在模型结果中的字段路径，用于定位错误。
        errors: 收集校验错误的列表。

    Returns:
        非空、不重复且全部合法的日期元组；校验失败时返回 None。
    """
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
    """解析模型返回的 Weekly JSON，并执行完整的来源与结构校验。

    从第一个 ``{`` 开始解析一个 JSON 对象。主条目必须使用允许的类型，覆盖全部
    ``legal_days`` 和 ``required`` 分区；主条目与 bypass 的来源日期都必须合法，
    bypass 也不能出现未知类别。

    Args:
        raw: 模型返回的原始文本。
        legal_days: 本批主条目和 bypass 可以引用的 Daily 日期。
        required: Daily 中出现过、主条目必须保留的 Weekly 分区。

    Returns:
        校验成功时返回结构化结果和空错误列表；任一错误出现时返回 None 和全部
        已收集的错误。
    """
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
    """生成一批 Daily 的 Weekly 候选，并校验其预算与覆盖范围。

    首次结构或预算校验失败时，会把错误加入提示并重试一次。模型调用抛出异常时
    立即失败，不再重试。

    Args:
        provider: 执行 Weekly 生成的模型客户端。
        sources: 同一提示批次中的完整 Daily 来源。

    Returns:
        两次机会内通过结构、来源、分区和预算校验的结果；否则返回 None。
    """
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
    """让模型合并多批产生的超预算主条目。

    首次校验失败时携带错误重试一次；模型调用抛出异常时立即失败。这里仅返回
    主条目，原批次生成的 bypass 由调用方原样保留。

    Args:
        provider: 执行合并的模型客户端。
        items: 各批生成并去重后的主条目。
        legal_days: 合并结果必须覆盖的全部 Daily 日期。
        required: 合并结果必须保留的 Weekly 分区。

    Returns:
        通过结构、覆盖和预算校验的主条目；两次均未通过时返回 None。
    """
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
    """合并类型相同且正文仅空白不同的重复主条目。

    Args:
        items: 按模型重要性顺序排列的主条目。

    Returns:
        保持首次出现位置和文本的条目；重复项的来源日期按首次出现顺序合并。
    """
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
    """从全部 Daily 生成一份结构化 Weekly 候选。

    Daily 超出单次提示预算时按完整来源分批生成。各批主条目合并去重后，如果
    保留全部日期和分区仍超出正文预算，则再调用模型做一次全局合并。任一批或
    全局合并失败都会放弃整次生成；各批 bypass 不参与全局合并。

    Args:
        provider: 执行分批生成和必要时全局合并的模型客户端。
        sources: 按日期排列的全部 Daily；为空时不调用模型。

    Returns:
        全部批次成功时返回主条目和三类 bypass；任一步失败时返回 None。
    """
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
    """把主条目按“进展”“更正”“待续”的顺序渲染为 Weekly。

    Args:
        period: 写入一级标题的 ISO 周标识。
        items: 已校验类型的 Weekly 主条目。

    Returns:
        只包含非空分区的 Markdown 正文，并以换行结尾。
    """
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
    """在预算超限时整条删除候选，同时保护分区和来源日期覆盖。

    选择器优先从尾部删除 correction 和 open_loop，再删除其他类型；仅当同一
    分区和条目涉及的每个受保护日期都有其他条目代表时才可删除。无法继续删除
    时会返回当前结果，因此返回正文仍可能超过预算。

    Args:
        period: 渲染长度计算使用的 ISO 周标识。
        items: 按重要性顺序排列的主条目。
        budget: Weekly Markdown 正文的最大字符数。
        required_days: 必须继续被条目覆盖的日期；None 或空集合时保护条目中出现
            的全部日期。

    Returns:
        保持原顺序的完整条目列表。
    """
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
    """从尾部删除 bypass 条目，直到 Markdown 正文不超预算。

    Args:
        items: 按重要性从高到低排列的 bypass 条目。
        budget: bypass Markdown 正文的最大字符数。

    Returns:
        能完整放入预算的前缀；单条也超限时为空。
    """
    selected = list(items)
    while selected and len(render_bypass(selected)) > budget:
        selected.pop()
    return selected


def render_bypass(items: list[WeeklyBypassItem]) -> str:
    """把 bypass 条目渲染为 Markdown 无序列表。

    Args:
        items: 要按当前顺序渲染的 bypass 条目。

    Returns:
        每条一行的列表；非空结果以换行结尾，空输入返回空字符串。
    """
    return "\n".join(f"- {item.text}" for item in items) + ("\n" if items else "")


def weekly_item_to_dict(item: WeeklyItem) -> dict[str, object]:
    """把 Weekly 主条目转换为可写入 frontmatter 的字典。

    Args:
        item: 要序列化的主条目。

    Returns:
        保留类型、正文和来源日期的字典。
    """
    return {
        "type": item.type,
        "text": item.text,
        "source_days": list(item.source_days),
    }


def bypass_item_to_dict(item: WeeklyBypassItem) -> dict[str, object]:
    """把 Weekly bypass 条目转换为可写入 frontmatter 的字典。

    Args:
        item: 要序列化的 bypass 条目。

    Returns:
        保留正文和来源日期的字典。
    """
    return {"text": item.text, "source_days": list(item.source_days)}
