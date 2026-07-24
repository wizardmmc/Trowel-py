"""Episode segment 的 Markdown 编解码。"""

from __future__ import annotations

import hashlib
import re
from collections import OrderedDict
from typing import Any

from trowel_py.memory.draft import DraftDiary

from .codec import _coerce_meta_str

_DIARY_FIELDS = ("outcomes", "decisions", "corrections", "open_loops")
_SEG_START = re.compile(r"<!-- @segment (\S+) -->")
_SEG_END = re.compile(r"<!-- @endsegment (\S+) -->")


def _render_segment(
    segment_id: str, diary_entries: tuple[DraftDiary, ...]
) -> tuple[str, str, list[str], str]:

    dates: list[str] = []
    if diary_entries:
        inner_parts: list[str] = []
        for d in sorted(diary_entries, key=lambda x: x.date):
            dates.append(d.date)
            inner_parts.append(_render_date_block(d))
        inner = "\n".join(inner_parts)
        empty_reason = ""
    else:
        empty_reason = "agent distilled no diary events"
        inner = f"_empty_reason: {empty_reason}_\n"
    block = (
        f"<!-- @segment {segment_id} -->\n{inner}<!-- @endsegment {segment_id} -->\n"
    )
    content_hash = hashlib.sha256(inner.encode("utf-8")).hexdigest()[:16]
    return block, content_hash, dates, empty_reason


def _render_date_block(d: DraftDiary) -> str:

    sections: list[str] = []
    for field_name in _DIARY_FIELDS:
        items = getattr(d, field_name)
        if items:
            bullets = "\n".join(f"- {_single_line(it)}" for it in items)
            sections.append(f"#### {field_name}\n{bullets}")
    if sections:
        return f"## {d.date}\n\n" + "\n\n".join(sections) + "\n"
    if d.events.strip():
        return f"## {d.date}\n\n{d.events.rstrip()}\n"
    return f"## {d.date}\n"


def _single_line(text: str) -> str:

    return " ".join(text.split())


def _parse_structured_block(block_text: str, date: str) -> DraftDiary:

    has_sections = any(line.startswith("#### ") for line in block_text.splitlines())
    if not has_sections:
        return DraftDiary(date=date, events=block_text.strip())
    fields: dict[str, list[str]] = {f: [] for f in _DIARY_FIELDS}
    current: str | None = None
    for line in block_text.splitlines():
        if line.startswith("#### "):
            name = line[len("#### ") :].strip().lower()
            current = name if name in fields else None
        elif current is not None and line.startswith("- "):
            item = line[len("- ") :].strip()
            if item:
                fields[current].append(item)
    entry = DraftDiary(
        date=date,
        outcomes=tuple(fields["outcomes"]),
        decisions=tuple(fields["decisions"]),
        corrections=tuple(fields["corrections"]),
        open_loops=tuple(fields["open_loops"]),
    )
    if entry.outcomes or entry.decisions or entry.corrections or entry.open_loops:
        return entry

    return DraftDiary(date=date, events=block_text.strip())


def _parse_segment_blocks(body: str) -> "OrderedDict[str, str]":
    """结束 marker 必须同 id；不匹配的段丢弃，避免吞并相邻 segment。"""

    blocks: "OrderedDict[str, str]" = OrderedDict()
    pos = 0
    while True:
        m = _SEG_START.search(body, pos)
        if not m:
            break
        sid = m.group(1)

        search_from = m.end()
        while True:
            m2 = _SEG_END.search(body, search_from)
            if not m2:
                break
            if m2.group(1) == sid:
                break
            search_from = m2.end()
        if not m2 or m2.group(1) != sid:
            pos = m.end()
            continue
        blocks[sid] = body[m.start() : m2.end()] + "\n"
        pos = m2.end()
    return blocks


def _episode_covers_date(fm: dict[str, Any], date: str) -> bool:

    ad = fm.get("activity_dates")
    if ad:
        return date in [_coerce_meta_str(d) for d in ad]
    return _coerce_meta_str(fm.get("review_date")) == date


def _segment_entry_for_date(
    block: str, date: str, seg_meta: dict[str, Any]
) -> str | None:
    """有 activity_dates 时严格按元数据路由，否则从日期标题恢复。"""

    ad = seg_meta.get("activity_dates")
    if ad:
        ad_str = [_coerce_meta_str(d) for d in ad]
        if date not in ad_str:
            return None
    elif date not in _h2_headings(block):
        return None
    return _extract_h2_block(block, date)


def _h2_headings(block: str) -> list[str]:

    return [ln.strip()[3:] for ln in block.splitlines() if ln.startswith("## ")]


def _extract_h2_block(block: str, date: str) -> str | None:

    target = f"## {date}"
    out: list[str] = []
    capturing = False
    for ln in block.splitlines():
        if capturing:
            if ln.startswith("## ") or ln.startswith("<!-- @endsegment"):
                break
            out.append(ln)
        elif ln.strip() == target:
            capturing = True
    text = "\n".join(out).strip()
    return text or None
