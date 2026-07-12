"""LLM diary compression for the three layers (slice-041).

daily (review_job each night): episodes → ≤800-char compressed daily, keep
gotcha/pain/decision, drop flow. weekly (tidy --weekly): this ISO week's
dailies → ≤800-char weekly + three bypass files (span越大越流水). monthly
(tidy --monthly): this month's weeklies → ≤800-char monthly, flow-only.

Three bypass categories (technical-detail / emotional-trigger / cross-week-
causal) are week-level only (S3 — never enter monthly). The weekly prompt
splits content into the four routes in one JSON call; bypass bodies land in
``diary/bypass/<category>/<YYYY-Www>.md``.

Each layer caps input to keep the LLM call bounded; the 800-char cap is a
prompt instruction (soft, like the injection budget).
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from trowel_py.llm.client import LLMProvider
from trowel_py.memory.store import MemoryStore, _dump_frontmatter, _split_frontmatter

#: cap the raw body fed to the LLM (chars) so a huge day doesn't blow tokens.
_INPUT_CAP = 8000
#: W4 (codex): hard cap on compressed output. The prompt asks for <=800 but a
#: verbose model can exceed it; truncate on persist so bloat doesn't propagate
#: to weekly/monthly or inflate injection.
_OUTPUT_CAP = 800


def _cap(text: str) -> str:
    """Hard-cap ``text`` to ``_OUTPUT_CAP`` chars with an ellipsis marker."""
    if len(text) <= _OUTPUT_CAP:
        return text
    return text[:_OUTPUT_CAP - 1].rstrip() + "…"

#: the three bypass categories (S3). Order is stable for output.
BYPASS_CATEGORIES = ("technical-detail", "emotional-trigger", "cross-week-causal")

_DAILY_SYS = (
    "你是日记压缩器。把一天的 cc 经历压缩成 ≤800 字 markdown。"
    "保住 gotcha / 痛点 / 决策 / 洞察，丢流水。保人风格（情绪 / 场景 / 自我评估）。"
    "只输出压缩后的日记正文，不要解释。"
)
_DAILY_USER = "原始经历（按时间序）：\n{body}\n\n输出压缩日记（≤800字 markdown）："

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


def _episode_bodies_for_date(root: Path, date_str: str) -> list[tuple[str, str]]:
    """Return ``[(registered_at, body)]`` for every episode whose review_date
    matches, sorted by registered_at (time order). Empty when none match."""
    eps_dir = root / "episodes"
    if not eps_dir.exists():
        return []
    items: list[tuple[str, str]] = []
    for p in sorted(eps_dir.glob("*.md")):
        fm, body = _split_frontmatter(p.read_text(encoding="utf-8"))
        if not fm or fm.get("review_date") != date_str:
            continue
        items.append((str(fm.get("registered_at", "")), body))
    items.sort(key=lambda x: x[0])
    return items


def compress_daily(
    root: Path | str, date_str: str, provider: LLMProvider
) -> str:
    """Compress one day's episodes into ``diary/daily/<date>.md`` (≤800 chars).

    Returns the date stem, or ``""`` when no episode matches (no fabricated
    empty daily — preserves 039's "empty = nothing happened" contract).
    """
    root_path = Path(root)
    items = _episode_bodies_for_date(root_path, date_str)
    if not items:
        return ""
    raw = "\n\n".join(body for _ts, body in items)[:_INPUT_CAP]
    compressed = _cap(provider.complete(_DAILY_SYS, _DAILY_USER.format(body=raw)))
    MemoryStore(root_path).write_diary({
        "type": "diary", "date": date_str, "layer": "day", "period": date_str,
        "promoted_knowledge": [], "__body": compressed,
    })
    return date_str


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
