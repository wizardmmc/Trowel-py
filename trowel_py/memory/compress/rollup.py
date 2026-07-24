"""Weekly 与 monthly 日记压缩。"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from trowel_py.llm.client import LLMProvider
from trowel_py.memory.store import MemoryStore, _dump_frontmatter

_INPUT_CAP = 8000
_OUTPUT_CAP = 800

BYPASS_CATEGORIES = (
    "technical-detail",
    "emotional-trigger",
    "cross-week-causal",
)

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


def _cap(text: str) -> str:
    if len(text) <= _OUTPUT_CAP:
        return text
    return text[: _OUTPUT_CAP - 1].rstrip() + "…"


def _parse_iso_week(s: str) -> tuple[int, int]:
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
    """把目标 ISO week 的 daily 压缩为 weekly 与三类 bypass。"""
    root_path = Path(root)
    iso_year, iso_week_num = _parse_iso_week(iso_week)
    store = MemoryStore(root_path)
    dailies = [
        d
        for d in store.load_diary(layer="day")
        if _in_iso_week(d.date, iso_year, iso_week_num)
    ]
    if not dailies:
        return {
            "weekly_written": False,
            "iso_week": iso_week,
            "bypass": {c: False for c in BYPASS_CATEGORIES},
        }
    dailies.sort(key=lambda d: d.date)
    raw = "\n\n".join(f"## {d.date}\n{d.body}" for d in dailies)[:_INPUT_CAP]
    llm_out = provider.complete(_WEEKLY_SYS, _WEEKLY_USER.format(body=raw))
    parsed = _parse_weekly_output(llm_out)
    store.write_diary(
        {
            "type": "diary",
            "date": iso_week,
            "layer": "week",
            "period": iso_week,
            "promoted_knowledge": [],
            "__body": _cap(parsed["weekly"]),
        }
    )
    bypass_written: dict[str, bool] = {}
    for cat in BYPASS_CATEGORIES:
        body = parsed["bypass"].get(cat, "")
        if body:
            _write_bypass(root_path, cat, iso_week, _cap(body))
            bypass_written[cat] = True
        else:
            bypass_written[cat] = False
    return {
        "weekly_written": True,
        "iso_week": iso_week,
        "bypass": bypass_written,
    }


def _week_in_month(iso_week_str: str, month: str) -> bool:
    """以 ISO week 的周一决定它归属的月份。"""
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
    """把周一归属于目标月份的 weekly 压缩为 monthly。"""
    root_path = Path(root)
    store = MemoryStore(root_path)
    weeklies = [
        w
        for w in store.load_diary(layer="week")
        if _week_in_month(w.period or w.date, month)
    ]
    if not weeklies:
        return {"monthly_written": False, "month": month}
    weeklies.sort(key=lambda d: d.date)
    raw = "\n\n".join(
        f"## {w.period or w.date}\n{w.body}" for w in weeklies
    )[:_INPUT_CAP]
    compressed = _cap(
        provider.complete(_MONTHLY_SYS, _MONTHLY_USER.format(body=raw))
    )
    store.write_diary(
        {
            "type": "diary",
            "date": month,
            "layer": "month",
            "period": month,
            "promoted_knowledge": [],
            "__body": compressed,
        }
    )
    return {"monthly_written": True, "month": month}


def _parse_weekly_output(raw: str) -> dict[str, Any]:
    """解析 weekly/bypass JSON；非 JSON 才降级为纯 weekly。"""
    start = raw.find("{")
    if start < 0:
        return {"weekly": raw.strip(), "bypass": {}}
    try:
        data, _end = json.JSONDecoder().raw_decode(raw[start:])
    except json.JSONDecodeError:
        return {"weekly": raw.strip(), "bypass": {}}
    # JSON 中的空 weekly 必须保持为空，避免把 bypass 原文混入 weekly。
    weekly = str(data.get("weekly", "")).strip()
    bypass_raw = data.get("bypass") or {}
    bypass: dict[str, str] = {}
    if isinstance(bypass_raw, dict):
        for cat in BYPASS_CATEGORIES:
            body = str(bypass_raw.get(cat, "")).strip()
            if body:
                bypass[cat] = body
    return {"weekly": weekly, "bypass": bypass}


def _write_bypass(
    root: Path, category: str, iso_week: str, body: str
) -> Path:
    """用独立 type 落盘，阻止 bypass 进入 diary 压缩链。"""
    path = root / "diary" / "bypass" / category / f"{iso_week}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    fm = {"type": "bypass", "category": category, "period": iso_week}
    path.write_text(_dump_frontmatter(fm, body.strip() + "\n"), encoding="utf-8")
    return path
