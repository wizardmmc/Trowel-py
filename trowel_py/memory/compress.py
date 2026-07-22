"""LLM diary compression for the three layers.

daily (slice-062 rewrite): the LLM no longer emits the final Markdown. It emits
typed items (each citing a source segment id); Python validates the schema +
source, dedupes, budget-selects and renders the fixed 进展/更正/待续 Markdown
(≤800 chars, whole-bullet trims only). Failure writes a
``generation_status=fallback`` notice that points at the source segments — never
the full aggregate (C-7). ``source_hash`` idempotence skips the LLM when the
structured inputs are unchanged and the existing daily still validates.

weekly (tidy --weekly, slice-041): this ISO week's dailies → ≤800-char weekly +
three bypass files (span越大越流水). monthly (tidy --monthly): this month's
weeklies → ≤800-char monthly, flow-only. weekly/monthly cap raw input
(``_INPUT_CAP``) and hard-cap output (``_cap``); daily stopped capping in
slice-052 and now hard-selects whole bullets (slice-062 C-3).

Three bypass categories (technical-detail / emotional-trigger / cross-week-
causal) are week-level only (S3 — never enter monthly). The weekly prompt splits
content into the four routes in one JSON call; bypass bodies land in
``diary/bypass/<category>/<YYYY-Www>.md``.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from trowel_py.llm.client import LLMProvider
from trowel_py.memory.prompt import (
    DAILY_ITEM_TYPES,
    build_daily_compress_prompt,
)
from trowel_py.memory.store import MemoryStore, _dump_frontmatter, _split_frontmatter

logger = logging.getLogger(__name__)

#: slice-062 daily body char budget (contract 2 / C-3). Counts the rendered
#: Markdown body INCLUDING the ``# date`` title and ``## section`` headers,
#: excluding frontmatter. Enforced by whole-bullet selection — never a
#: mid-sentence cut.
_DAILY_BUDGET = 800
#: Bump when prompt/selection semantics change so old ``ok`` dailies are not
#: kept forever by source-hash idempotence. Version 2 adds short source aliases
#: and guarantees one surviving item per represented section.
_DAILY_GENERATION_VERSION = 2
#: weekly/monthly raw-body cap fed to the LLM (chars). Daily does not cap input
#: (the structured projection is already the compressed experience track).
_INPUT_CAP = 8000
#: W4 (codex): hard cap on WEEKLY/MONTHLY compressed output. Daily enforces its
#: budget via whole-bullet selection instead (slice-062 C-3).
_OUTPUT_CAP = 800

#: item type → daily section. outcome + decision merge into 进展.
_SECTION_FOR_TYPE: dict[str, str] = {
    "outcome": "进展",
    "decision": "进展",
    "correction": "更正",
    "open_loop": "待续",
}
#: stable render order for the three sections (empty ones omitted).
_SECTION_ORDER = ("进展", "更正", "待续")
#: budget priority (contract 4): corrections + open loops outrank outcomes +
#: decisions, so the latter are dropped first when trimming. Higher = keep first.
_TYPE_PRIORITY: dict[str, int] = {
    "correction": 1,
    "open_loop": 1,
    "outcome": 0,
    "decision": 0,
}


@dataclass(frozen=True)
class _DailyItem:
    """One typed daily summary item emitted by the LLM (slice-062).

    Attributes:
        type: one of ``DAILY_ITEM_TYPES`` (outcome/decision/correction/open_loop).
        text: a complete, self-contained sentence.
        source: the segment_id this item was derived from (must be a real
            contributing segment — validated, never hallucinated).
    """

    type: str
    text: str
    source: str


def _cap(text: str) -> str:
    """Hard-cap ``text`` to ``_OUTPUT_CAP`` chars with an ellipsis marker."""
    if len(text) <= _OUTPUT_CAP:
        return text
    return text[:_OUTPUT_CAP - 1].rstrip() + "…"

#: the three bypass categories (S3). Order is stable for output.
BYPASS_CATEGORIES = ("technical-detail", "emotional-trigger", "cross-week-causal")


# --------------------------- daily (slice-062) ---------------------------


def _source_hash(sources: list[tuple[str, str, Any]]) -> str:
    """Stable hash of the structured daily inputs (idempotence key, contract 6).

    Covers every contributing segment id and its structured items (plus legacy
    ``events``), in registered_at order. Any change to the inputs changes the
    hash → the daily is rebuilt; an unchanged hash lets the existing daily stand.
    """
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
    """Map short prompt aliases to real segment ids in source order.

    Long ``<uuid>:<start>:<end>`` ids proved brittle in production: GLM copied
    only the UUID on both attempts and a valid 2026-07-18 daily fell back. The
    model only needs a citation handle, so the prompt exposes S1/S2 while
    Python keeps the authoritative mapping.
    """
    return {
        f"S{index}": seg_id
        for index, (seg_id, _registered_at, _entry) in enumerate(sources, 1)
    }


def _required_sections(sources: list[tuple[str, str, Any]]) -> set[str]:
    """Sections that structured source fields prove must appear in the daily."""
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
    """Render structured sources with short, model-copyable aliases."""
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
    """Parse one LLM response into validated items + per-item errors (contract 4).

    An item is rejected (counted as an error, dropped) when its type is unknown,
    its text is empty, or its source is not one of the contributing segments. A
    non-JSON response yields a single ``invalid JSON`` error. Empty list ``[]``
    means every item passed.
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
        # Accept exact real ids for backward compatibility with recorded tests
        # and older providers. New prompts expose aliases only.
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
    """Run the LLM (with one retry on validation failure) → (items, status).

    status is ``ok`` when a clean response was obtained, else ``fallback``. A
    provider exception at either attempt degrades straight to ``fallback``
    (contract 5). The retry feeds the concrete errors back so the model can fix
    a bad source / schema (contract 4 step 5).
    """
    sys_prompt = (
        "你是日记压缩器。把当天结构化经历压缩成可回忆的当天摘要，输出带 source 的结构化 items。"
        "只输出 JSON 对象，不要 markdown，不要解释。"
    )
    try:
        raw1 = provider.complete(
            sys_prompt, build_daily_compress_prompt(
                date=date_str, sources_block=sources_block
            )
        )
    except Exception:  # noqa: BLE001 — provider failure is a fallback, not a crash
        logger.warning("daily %s: provider call failed", date_str, exc_info=True)
        return [], "fallback"
    items1, errs1 = _parse_and_validate(
        raw1, source_aliases, required_sections
    )
    if not errs1:
        return items1, "ok"
    logger.info("daily %s: first response rejected (%s) — retrying", date_str, errs1[:3])
    try:
        retry_prompt = build_daily_compress_prompt(
            date=date_str, sources_block=sources_block
        ) + "\n\n【上次返回被拒绝，具体错误】\n- " + "\n- ".join(errs1)
        raw2 = provider.complete(sys_prompt, retry_prompt)
    except Exception:  # noqa: BLE001
        logger.warning("daily %s: provider retry failed", date_str, exc_info=True)
        return [], "fallback"
    items2, errs2 = _parse_and_validate(
        raw2, source_aliases, required_sections
    )
    if errs2:
        logger.warning("daily %s: retry still rejected (%s) — fallback", date_str, errs2[:3])
        return [], "fallback"
    return items2, "ok"


def _normalize(text: str) -> str:
    """Collapse whitespace for an exact-dedup key (the stored text is untouched)."""
    return re.sub(r"\s+", "", text).strip()


def _dedup_items(items: list[_DailyItem]) -> list[_DailyItem]:
    """Drop exact-duplicate text, keeping the first occurrence (contract 4)."""
    seen: set[str] = set()
    out: list[_DailyItem] = []
    for it in items:
        key = _normalize(it.text)
        if key and key not in seen:
            seen.add(key)
            out.append(it)
    return out


def _render_daily_body(date_str: str, items: list[_DailyItem]) -> str:
    """Render the fixed 进展/更正/待续 Markdown (empty sections omitted, contract 2)."""
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
    """Trim items to fit the body budget by dropping WHOLE bullets (C-3).

    Keep at least one item from every represented section, then drop removable
    low-priority items (outcome/decision) first, latest-first. This prevents a
    busy day from losing its entire 进展 section when corrections/open loops are
    numerous. If one item per section still exceeds the budget, the caller
    writes a fallback rather than silently starving a section or truncating.
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
            i for i, it in enumerate(selected)
            if section_counts.get(_SECTION_FOR_TYPE.get(it.type, ""), 0) > 1
        ]
        if not removable:
            break
        low_priority = [
            i for i in removable
            if _TYPE_PRIORITY.get(selected[i].type, 0) == 0
        ]
        drop = low_priority[-1] if low_priority else removable[-1]
        selected.pop(drop)
    return selected


def _existing_daily_usable(root_path: Path, date_str: str, shash: str) -> bool:
    """Whether an existing successful daily can survive a failed regeneration."""
    path = root_path / "diary" / "daily" / f"{date_str}.md"
    if not path.exists():
        return False
    fm, body = _split_frontmatter(path.read_text(encoding="utf-8"))
    return bool(
        fm
        and fm.get("generation_status") == "ok"
        and fm.get("source_hash") == shash
        and len(body.strip()) <= _DAILY_BUDGET
    )


def _existing_daily_ok(root_path: Path, date_str: str, shash: str) -> bool:
    """Idempotence gate (contract 6): keep a current-version daily unchanged.

    True only when an ``ok`` daily exists with a matching ``source_hash`` whose
    body still fits the budget and whose generation version is current. A
    missing, stale, or ``fallback`` daily rebuilds.
    """
    path = root_path / "diary" / "daily" / f"{date_str}.md"
    if not _existing_daily_usable(root_path, date_str, shash):
        return False
    fm, body = _split_frontmatter(path.read_text(encoding="utf-8"))
    if not fm:
        return False
    if fm.get("generation_version") != _DAILY_GENERATION_VERSION:
        return False
    return len(body.strip()) <= _DAILY_BUDGET


def _write_daily(
    store: MemoryStore, date_str: str, body: str,
    source_segments: list[str], shash: str, status: str,
) -> None:
    """Persist one daily with its slice-062 provenance frontmatter."""
    store.write_diary({
        "type": "diary",
        "date": date_str,
        "layer": "day",
        "period": date_str,
        "promoted_knowledge": [],
        "source_segments": source_segments,
        "source_hash": shash,
        "generated_at": datetime.now().isoformat(),
        "generation_status": status,
        "generation_version": _DAILY_GENERATION_VERSION,
        "__body": body,
    })


def _write_fallback_body(
    store: MemoryStore, date_str: str, source_segments: list[str], shash: str
) -> None:
    """Write a ``generation_status=fallback`` notice (contract 5 / C-7).

    The notice names the source segments so the raw experience is recoverable,
    but never echoes the experience itself — a failed compression must not be
    disguised as a summary by dumping the full aggregate.
    """
    seg_list = "\n".join(f"- {s}" for s in source_segments) or "- (无来源)"
    body = (
        f"# {date_str}\n\n"
        f"当日未生成可用摘要（fallback）。原始结构化经历保存在以下 episode segment，"
        f"下次 review/tidy 会重试：\n{seg_list}\n"
    )
    _write_daily(store, date_str, body, source_segments, shash, "fallback")


def compress_daily(
    root: Path | str, date_str: str, provider: LLMProvider
) -> str:
    """Compress one day's structured episodes into ``diary/daily/<date>.md``.

    slice-062 pipeline: project the day's structured sources → idempotence gate
    on ``source_hash`` → LLM emits typed items (citing source segments) → Python
    validates + dedupes + budget-selects → render fixed Markdown. On any failure
    (provider down, unparseable / un-sourced output after one retry) a fallback
    notice is written instead (C-7 — never the full aggregate).

    Returns the date stem, or ``""`` when no episode matches (no fabricated
    empty daily — preserves 039's "empty = nothing happened" contract).
    """
    root_path = Path(root)
    store = MemoryStore(root_path)
    sources = store.project_daily_sources(date_str)
    if not sources:
        return ""
    source_segments = sorted({seg_id for seg_id, _reg, _entry in sources})
    shash = _source_hash(sources)
    if _existing_daily_ok(root_path, date_str, shash):
        return date_str
    preserve_on_failure = _existing_daily_usable(root_path, date_str, shash)

    aliases = _source_aliases(sources)
    sources_block = _render_sources_block(sources, aliases)
    items, status = _generate_items(
        provider,
        date_str,
        sources_block,
        aliases,
        _required_sections(sources),
    )
    if status == "ok" and items:
        items = _dedup_items(items)
        items = _select_within_budget(date_str, items)
        body = _render_daily_body(date_str, items)
        if len(body) <= _DAILY_BUDGET:
            _write_daily(store, date_str, body, source_segments, shash, "ok")
            return date_str
        # C-3: still over budget after dropping whole bullets — a single item
        # alone exceeds it (can't trim without truncating). Fall back rather
        # than persist an over-budget "ok" daily, which would also defeat
        # idempotence (_existing_daily_ok rejects a >800 body) and re-call the
        # LLM on every rerun.
        logger.warning(
            "daily %s: body still %d chars after whole-bullet selection; fallback",
            date_str, len(body),
        )
    # A version/prompt upgrade must not replace a still-usable old summary with
    # a fallback notice. It remains stale and the rebuild scan retries later.
    if preserve_on_failure:
        logger.warning(
            "daily %s: regeneration failed; preserving previous usable daily",
            date_str,
        )
        return date_str
    # fallback (contract 5): compression failed, yielded nothing usable, or a
    # single oversized item could not fit the budget.
    _write_fallback_body(store, date_str, source_segments, shash)
    return date_str


def write_fallback_daily(root: Path | str, date_str: str) -> str:
    """Write a fallback daily without an LLM (the no-provider path, contract 5).

    Used when no LLM provider is configured: the day still gets a traceable
    fallback daily (not the full aggregate) so injection stays non-empty and the
    next review/tidy can retry. Returns the date stem, or ``""`` when no episode
    matches.
    """
    root_path = Path(root)
    store = MemoryStore(root_path)
    sources = store.project_daily_sources(date_str)
    if not sources:
        return ""
    source_segments = sorted({seg_id for seg_id, _reg, _entry in sources})
    shash = _source_hash(sources)
    _write_fallback_body(store, date_str, source_segments, shash)
    return date_str


def daily_dates_needing_rebuild(root: Path | str) -> list[str]:
    """Return episode dates whose daily is missing, failed, stale, or changed.

    The review loop previously compressed only dates touched by a newly
    distilled segment. Consequently a fallback (2026-07-18), a legacy wrong
    daily (2026-07-17), or a day with episodes but no daily (2026-07-12) could
    survive forever once no new segment touched that date. This read-only scan
    makes those derived caches retryable on the next provider-backed review.

    Returns:
        Sorted ISO date strings that ``compress_daily`` should revisit.
    """
    root_path = Path(root)
    episodes_dir = root_path / "episodes"
    dates: set[str] = set()
    if episodes_dir.exists():
        for path in sorted(episodes_dir.glob("*.md")):
            fm, body = _split_frontmatter(path.read_text(encoding="utf-8"))
            if not fm:
                continue
            dates.update(
                str(value)
                for value in (fm.get("activity_dates") or [])
                if re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(value))
            )
            review_date = str(fm.get("review_date") or "")
            if re.fullmatch(r"\d{4}-\d{2}-\d{2}", review_date):
                dates.add(review_date)
            for segment in fm.get("segments") or []:
                if not isinstance(segment, dict):
                    continue
                dates.update(
                    str(value)
                    for value in (segment.get("activity_dates") or [])
                    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(value))
                )
            dates.update(
                match.group(1)
                for match in re.finditer(
                    r"^## (\d{4}-\d{2}-\d{2})\s*$", body, re.MULTILINE
                )
            )

    store = MemoryStore(root_path)
    needs: list[str] = []
    for date_str in sorted(dates):
        sources = store.project_daily_sources(date_str)
        if not sources:
            continue
        shash = _source_hash(sources)
        if not _existing_daily_ok(root_path, date_str, shash):
            needs.append(date_str)
    return needs


# --------------------------- weekly/monthly (slice-041) ---------------------------

_WEEKLY_SYS = (
    "你是周记压缩器。把本周每天的日记压缩成 ≤800 字周记 + 三类旁路。"
    "周记：保事件流主线 + 指向旁路的引用，跨度大更流水（记事件，少记 gotcha——gotcha 靠笔记晋升 core，不靠周记保真）。"
    "三类旁路（只做日→周，不进月）：technical-detail（技术细节）/ emotional-trigger（情感触发场景）/ cross-week-causal（跨周因果链）——"
    "这三类不进周记正文，分别写进对应旁路文件（每类 ≤800 字）。"
    '输出 JSON: {"weekly":"...","bypass":{"technical-detail":"...","emotional-trigger":"...","cross-week-causal":"..."}}，'
    "某类无内容则空字符串。只输出 JSON，不要解释。"
)
_WEEKLY_USER = "本周日记（按日期序）：\n{body}\n\n输出 JSON（周记 + 三类旁路）："

_MONTHLY_SYS = (
    "你是月记压缩器。把本月的周记压缩成 ≤800 字月记。"
    "跨度更大，更流水（记主线事件流，不记 gotcha——gotcha 靠笔记晋升 core，不靠月记保真）。"
    "只输出月记正文，不要解释。"
)
_MONTHLY_USER = "本月周记（按周序）：\n{body}\n\n输出压缩月记（≤800字 markdown）："


def _parse_iso_week(s: str) -> tuple[int, int]:
    """``"2026-W28"`` → ``(2026, 28)``."""
    m = re.match(r"(\d{4})-W(\d{2})", s)
    if not m:
        raise ValueError(f"bad ISO week string: {s!r}")
    return int(m.group(1)), int(m.group(2))


def _in_iso_week(date_str: str, iso_year: int, iso_week: int) -> bool:
    from datetime import date as _date

    try:
        d = _date.fromisoformat(date_str)
    except ValueError:
        return False
    y, w, _ = d.isocalendar()
    return y == iso_year and w == iso_week


def compress_weekly(
    root: Path | str, iso_week: str, provider: LLMProvider
) -> dict[str, Any]:
    """Compress one ISO week's dailies into a weekly + three bypass files.

    The LLM emits one JSON object ``{"weekly": ..., "bypass": {...}}``; Python
    writes ``diary/weekly/<iso_week>.md`` and ``diary/bypass/<category>/
    <iso_week>.md`` for each non-empty bypass category. A non-JSON response
    degrades to weekly-only (raw text as the weekly body, no bypass).

    Returns ``{"weekly_written": bool, "iso_week": str, "bypass": {cat: bool}}``.
    """
    root_path = Path(root)
    iso_year, iso_week_num = _parse_iso_week(iso_week)
    store = MemoryStore(root_path)
    dailies = [
        d for d in store.load_diary(layer="day")
        if _in_iso_week(d.date, iso_year, iso_week_num)
    ]
    if not dailies:
        return {"weekly_written": False, "iso_week": iso_week,
                "bypass": {c: False for c in BYPASS_CATEGORIES}}
    dailies.sort(key=lambda d: d.date)
    raw = "\n\n".join(f"## {d.date}\n{d.body}" for d in dailies)[:_INPUT_CAP]
    llm_out = provider.complete(_WEEKLY_SYS, _WEEKLY_USER.format(body=raw))
    parsed = _parse_weekly_output(llm_out)
    store.write_diary({
        "type": "diary", "date": iso_week, "layer": "week", "period": iso_week,
        "promoted_knowledge": [], "__body": _cap(parsed["weekly"]),
    })
    bypass_written: dict[str, bool] = {}
    for cat in BYPASS_CATEGORIES:
        body = parsed["bypass"].get(cat, "")
        if body:
            _write_bypass(root_path, cat, iso_week, _cap(body))
            bypass_written[cat] = True
        else:
            bypass_written[cat] = False
    return {"weekly_written": True, "iso_week": iso_week, "bypass": bypass_written}


def _week_in_month(iso_week_str: str, month: str) -> bool:
    """True if the Monday of this ISO week falls in ``month`` (YYYY-MM).

    An ISO week can span two calendar months (e.g. W28 might start in late
    June); we attribute it to the month of its Monday — deterministic.
    """
    from datetime import date as _date

    try:
        y, w = _parse_iso_week(iso_week_str)
        monday = _date.fromisocalendar(y, w, 1)
        return monday.strftime("%Y-%m") == month
    except (ValueError, TypeError):
        return False


def compress_monthly(
    root: Path | str, month: str, provider: LLMProvider
) -> dict[str, Any]:
    """Compress one month's weeklies into ``diary/monthly/<month>.md``.

    ``month`` is ``YYYY-MM``. Reads every weekly whose Monday falls in that
    month, compresses to ≤800 chars (flow-only — span越大越流水).
    Returns ``{"monthly_written": bool, "month": str}``.
    """
    root_path = Path(root)
    store = MemoryStore(root_path)
    weeklies = [
        w for w in store.load_diary(layer="week")
        if _week_in_month(w.period or w.date, month)
    ]
    if not weeklies:
        return {"monthly_written": False, "month": month}
    weeklies.sort(key=lambda d: d.date)
    raw = "\n\n".join(
        f"## {w.period or w.date}\n{w.body}" for w in weeklies
    )[:_INPUT_CAP]
    compressed = _cap(provider.complete(_MONTHLY_SYS, _MONTHLY_USER.format(body=raw)))
    store.write_diary({
        "type": "diary", "date": month, "layer": "month", "period": month,
        "promoted_knowledge": [], "__body": compressed,
    })
    return {"monthly_written": True, "month": month}


def _parse_weekly_output(raw: str) -> dict[str, Any]:
    """Parse ``{"weekly": ..., "bypass": {...}}`` JSON; fall back to weekly-only.

    W4 (auto-cr): ``raw_decode`` from the first ``{`` instead of a greedy
    ``\\{.*\\}`` regex — the latter would swallow trailing text containing
    ``}`` and yield non-JSON, degrading to weekly-only with garbage in the body.
    """
    start = raw.find("{")
    if start < 0:
        return {"weekly": raw.strip(), "bypass": {}}
    try:
        data, _end = json.JSONDecoder().raw_decode(raw[start:])
    except json.JSONDecodeError:
        return {"weekly": raw.strip(), "bypass": {}}
    # W5 (codex): once JSON parsed, honor an empty weekly — falling back to
    # ``raw.strip()`` here would embed the whole JSON (including bypass bodies)
    # into the weekly diary, leaking bypass content past C-2's isolation.
    weekly = str(data.get("weekly", "")).strip()
    bypass_raw = data.get("bypass") or {}
    bypass: dict[str, str] = {}
    if isinstance(bypass_raw, dict):
        for cat in BYPASS_CATEGORIES:
            body = str(bypass_raw.get(cat, "")).strip()
            if body:
                bypass[cat] = body
    return {"weekly": weekly, "bypass": bypass}


def _write_bypass(root: Path, category: str, iso_week: str, body: str) -> Path:
    """Write one bypass file: ``diary/bypass/<category>/<iso_week>.md``.

    Uses ``type="bypass"`` (not ``"diary"``) so ``load_diary`` skips it —
    bypass files are NOT part of the day/week/month compression chain (S3:
    they only do day→week, never enter monthly). The weekly body carries
    references to these files; they are read on demand, not injected.
    """
    path = root / "diary" / "bypass" / category / f"{iso_week}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    fm = {
        "type": "bypass", "category": category, "period": iso_week,
    }
    path.write_text(_dump_frontmatter(fm, body.strip() + "\n"), encoding="utf-8")
    return path
