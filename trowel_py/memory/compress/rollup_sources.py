"""Weekly/monthly 的完整上游来源扫描与稳定 hash。"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from trowel_py.memory.store import _split_frontmatter


@dataclass(frozen=True)
class PeriodSource:
    period: str
    body: str
    frontmatter: dict[str, Any]


def parse_iso_week(value: str) -> tuple[int, int]:
    match = re.fullmatch(r"(\d{4})-W(\d{2})", value)
    if not match:
        raise ValueError(f"bad ISO week string: {value!r}")
    year, week = int(match.group(1)), int(match.group(2))
    date.fromisocalendar(year, week, 1)
    return year, week


def in_iso_week(date_str: str, iso_year: int, iso_week: int) -> bool:
    try:
        parsed = date.fromisoformat(date_str)
    except ValueError:
        return False
    year, week, _weekday = parsed.isocalendar()
    return year == iso_year and week == iso_week


def week_in_month(iso_week: str, month: str) -> bool:
    """以 ISO week 的周一决定它归属的月份。"""
    try:
        year, week = parse_iso_week(iso_week)
        return date.fromisocalendar(year, week, 1).strftime("%Y-%m") == month
    except (TypeError, ValueError):
        return False


def diary_path(root: Path, layer: str, period: str) -> Path:
    directory = {"day": "daily", "week": "weekly", "month": "monthly"}[layer]
    return root / "diary" / directory / f"{period}.md"


def _load_source(root: Path, layer: str, period: str) -> PeriodSource | None:
    path = diary_path(root, layer, period)
    if not path.is_file():
        return None
    frontmatter, body = _split_frontmatter(path.read_text(encoding="utf-8"))
    if not frontmatter or frontmatter.get("type") != "diary":
        return None
    return PeriodSource(period, body, frontmatter)


def weekly_sources(root: Path | str, iso_week: str) -> list[PeriodSource]:
    year, week = parse_iso_week(iso_week)
    root_path = Path(root)
    daily_dir = root_path / "diary" / "daily"
    if not daily_dir.is_dir():
        return []
    sources: list[PeriodSource] = []
    for path in sorted(daily_dir.glob("*.md")):
        if not in_iso_week(path.stem, year, week):
            continue
        source = _load_source(root_path, "day", path.stem)
        if source is not None:
            sources.append(source)
    return sources


def monthly_sources(root: Path | str, month: str) -> list[PeriodSource]:
    root_path = Path(root)
    weekly_dir = root_path / "diary" / "weekly"
    if not weekly_dir.is_dir():
        return []
    sources: list[PeriodSource] = []
    for path in sorted(weekly_dir.glob("*.md")):
        if not week_in_month(path.stem, month):
            continue
        source = _load_source(root_path, "week", path.stem)
        if source is not None:
            sources.append(source)
    return sources


def source_hash(sources: list[PeriodSource]) -> str:
    """只纳入会改变下游语义或生成契约的稳定来源字段。"""
    payload = [
        {
            "period": source.period,
            "body": source.body,
            "source_hash": source.frontmatter.get("source_hash"),
            "generation_status": source.frontmatter.get("generation_status"),
            "generation_version": source.frontmatter.get("generation_version"),
            "items": source.frontmatter.get("items"),
        }
        for source in sources
    ]
    encoded = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:16]
