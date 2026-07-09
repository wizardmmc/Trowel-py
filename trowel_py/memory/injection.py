"""assemble the cc system injection string (slice-039).

Reads layer-one (core items, filtering ``status == retired``) + dictionary L0
+ recent diary layers, concatenates into one string that the launcher passes
to cc's native ``--append-system-prompt`` at spawn. cc appends it to its
default system tail.

Architecture (spike 2026-07-09): injection goes through cc's native flag, NOT
through the reverse proxy. The proxy's identity-rewrite (slice-030) stays
untouched — verified: 智谱 returns 200, ``cache_read_input_tokens=39808``.
"""
from __future__ import annotations

import logging
from datetime import date, timedelta
from pathlib import Path

from trowel_py.memory.paths import resolve_memory_root
from trowel_py.memory.store import MemoryStore
from trowel_py.memory.types import Diary

logger = logging.getLogger(__name__)

#: soft token budget (C-6, ~4K Hanzi). Over-budget logs a warning, no truncation.
TOKEN_BUDGET = 8000
_RECENT_DAYS = 7   # daily diary recency window
_WEEKLY_DAYS = 30  # weekly diary recency window


def build_memory_injection(now: str, root: Path | str | None = None) -> str:
    """Assemble the memory injection string (layer-one + L0 + recent diary).

    Args:
        now: ISO date (``YYYY-MM-DD``) anchoring the diary recency windows.
        root: memory root; ``None`` resolves via ``paths.resolve_memory_root``.

    Returns:
        The injection string; empty string when there is nothing to inject
        (the launcher then adds no ``--append-system-prompt`` flag).

    Layer-one items with ``status == 'retired'`` are filtered out (C-1/C-2).
    Notes bodies are never injected (C-5) — they are read on demand via the
    dictionary L0 → L1 → body drill-down. Over ``TOKEN_BUDGET`` logs a warning
    and does NOT truncate (C-6 soft budget).
    """
    store = MemoryStore(root if root is not None else resolve_memory_root())
    sections: list[str] = []
    core = _render_core(store)
    if core:
        sections.append(core)
    l0 = _render_l0(store)
    if l0:
        sections.append(l0)
    diary = _render_diary(store, now)
    if diary:
        sections.append(diary)
    if not sections:
        return ""
    body = "\n\n".join(sections)
    estimated = _estimate_tokens(body)
    if estimated > TOKEN_BUDGET:
        logger.warning(
            "memory injection ~%d tokens exceeds soft budget %d (C-6; not truncated)",
            estimated,
            TOKEN_BUDGET,
        )
    return body


def _render_core(store: MemoryStore) -> str:
    """Layer-one imperatives, excluding retired items (C-1/C-2)."""
    items = [it for it in store.load_core_items() if it.status != "retired"]
    if not items:
        return ""
    lines = ["# 铁律（强制遵守）"]
    for i, it in enumerate(items, 1):
        lines.append(f"{i}. {it.imperative}")
    return "\n".join(lines)


def _render_l0(store: MemoryStore) -> str:
    """Dictionary L0 root index (the model drills down L1/body on demand)."""
    text = store.load_dictionary_L0().strip()
    if not text:
        return ""
    return f"# 领域索引（dictionary L0，按需下钻 L1/正文）\n{text}"


def _render_diary(store: MemoryStore, now: str) -> str:
    """Recent diary in three recency windows (daily / weekly / this-year monthly).

    A malformed ``now`` (not strict zero-padded ISO) logs a warning and skips
    only the diary section — layer-one and L0 are still injected, so a bad date
    never blanks the whole injection (defense-in-depth below service's catch).
    """
    try:
        today = date.fromisoformat(now)
    except ValueError:
        logger.warning("injection: malformed 'now' %r; skipping diary section", now)
        return ""
    daily_since = (today - timedelta(days=_RECENT_DAYS)).isoformat()
    weekly_since = (today - timedelta(days=_WEEKLY_DAYS)).isoformat()
    # this-year monthly subsumes the近1月 monthly window (year-start <= 30d ago).
    month_since = today.replace(month=1, day=1).isoformat()

    daily = store.load_diary(since=daily_since, layer="day")
    weekly = store.load_diary(since=weekly_since, layer="week")
    monthly = store.load_diary(since=month_since, layer="month")

    blocks: list[str] = []
    if daily:
        blocks.append("## 近 7 天（daily）\n" + _format_diary(daily))
    if weekly:
        blocks.append("## 近 1 月（weekly）\n" + _format_diary(weekly))
    if monthly:
        blocks.append("## 今年（monthly）\n" + _format_diary(monthly))
    if not blocks:
        return ""
    return "# 近期日记\n\n" + "\n\n".join(blocks)


def _format_diary(entries: list[Diary]) -> str:
    """One bullet per entry, newest first: ``- [date] body``."""
    lines: list[str] = []
    for d in sorted(entries, key=lambda e: e.date, reverse=True):
        body = d.body.strip()
        lines.append(f"- [{d.date}] {body}" if body else f"- [{d.date}]")
    return "\n".join(lines)


def _estimate_tokens(text: str) -> int:
    """Rough token estimate: CJK chars ~2 tokens, others ~4 chars/token."""
    cjk = sum(1 for ch in text if "一" <= ch <= "鿿")
    return cjk * 2 + (len(text) - cjk) // 4
