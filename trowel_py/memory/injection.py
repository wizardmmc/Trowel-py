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

from trowel_py.memory.compress import _in_iso_week, _week_in_month
from trowel_py.memory.paths import resolve_memory_root
from trowel_py.memory.profile import _FIELD_TO_TITLE
from trowel_py.memory.store import MemoryStore
from trowel_py.memory.types import Diary

logger = logging.getLogger(__name__)

#: slice-041 progressive window: this week's dailies + this month's weeklies
#: (minus this week) + last 6 months' monthlies (minus this month) + earlier.
#: Each entry ≤800 chars (grill 2026-07-11). Total ~24K tokens — raised from
#: 039's 8K soft budget (GLM 200K window absorbs it).
TOKEN_BUDGET = 30000


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
    profile = _render_profile(store)
    if profile:
        sections.append(profile)
    l0 = _render_l0(store)
    if l0:
        sections.append(l0)
    # slice-040-c C-2: always carry the memory root + retrieval tool usage so
    # the model knows where memory lives and how to query it (search→read).
    root_section = _render_memory_root(store.root)
    # W4 (codex): progressive truncation — if core+L0+diary+root exceeds
    # TOKEN_BUDGET, drop the lowest-priority diary layers first (earlier
    # monthlies → half-year → month weeklies), keeping week dailies (gotcha-rich).
    body = "\n\n".join(sections + [root_section])
    for layers in (4, 3, 2, 1):
        diary = _render_diary(store, now, include_layers=layers)
        body = "\n\n".join(
            s for s in sections + ([diary] if diary else []) + [root_section]
        )
        if _estimate_tokens(body) <= TOKEN_BUDGET:
            break
    estimated = _estimate_tokens(body)
    if estimated > TOKEN_BUDGET:
        logger.warning(
            "memory injection ~%d tokens exceeds soft budget %d even after truncation (C-6)",
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


def _render_profile(store: MemoryStore) -> str:
    """Render the user self-description profile section (slice-048).

    Five dims (ability/methodology/expression/goal/other) in canonical order,
    reusing ``profile._FIELD_TO_TITLE`` so the title mapping has a single source
    of truth. Only dims whose body is non-empty (``val.strip()``) render, each
    as ``## 标题\\n内容``. Returns ``""`` when the profile is empty (C-4) — the
    section is then omitted, never an empty ``# 用户画像`` heading.

    No try/except (C-2): failure semantics match ``_render_core``/``_render_l0``.
    ``store.load_profile`` does not raise on normal paths (missing/empty file →
    ``empty_profile()``; body parse is lenient). Any raw IO error propagates to
    ``service._spawn``'s whole-string net, which drops the entire injection and
    spawns cc bare — identical to how a core.md IO error behaves today.
    """
    p = store.load_profile()
    blocks = [
        f"## {_FIELD_TO_TITLE[field]}\n{getattr(p, field)}"
        for field in _FIELD_TO_TITLE
        if getattr(p, field).strip()
    ]
    if not blocks:
        return ""
    return "# 用户画像\n\n" + "\n\n".join(blocks)


def _render_l0(store: MemoryStore) -> str:
    """Dictionary L0 root index (the model drills down L1/body on demand)."""
    text = store.load_dictionary_L0().strip()
    if not text:
        return ""
    return f"# 领域索引（dictionary L0，按需下钻 L1/正文）\n{text}"


def _render_memory_root(root: Path) -> str:
    """Memory root absolute path + retrieval tool usage (slice-040-c C-2).

    Always injected (even when core/L0/diary are empty) so the model knows
    where memory lives and how to call the MCP tools.
    """
    return (
        "# memory 根路径 + 检索\n"
        f"根：{root.resolve()}\n"
        "查笔记：memory.search(query) → memory.read(uri)"
    )


def _render_diary(store: MemoryStore, now: str, *, include_layers: int = 4) -> str:
    """Progressive recency window (slice-041 grill 2026-07-11).

    ``include_layers`` controls which month tiers are included (W4 codex
    truncation priority): 4=all, 3=drop earlier, 2=drop earlier+half-year,
    1=only this week's dailies (gotcha-rich, always kept).

    Four tiers, each ≤800 chars per entry, span越大越流水:
    - this week's dailies (keep gotcha — span small)
    - this month's weeklies minus this week (flow-ish)
    - last 6 months' monthlies minus this month (flow)
    - earlier monthlies (pure flow, if any)

    A malformed ``now`` logs a warning and skips only the diary section —
    layer-one and L0 are still injected.
    """
    try:
        today = date.fromisoformat(now)
    except ValueError:
        logger.warning("injection: malformed 'now' %r; skipping diary section", now)
        return ""
    iso_year, iso_week, _ = today.isocalendar()
    this_week = f"{iso_year}-W{iso_week:02d}"
    this_month = today.strftime("%Y-%m")
    six_months_ago = (today - timedelta(days=180)).strftime("%Y-%m")

    week_dailies = [
        d for d in store.load_diary(layer="day")
        if _in_iso_week(d.date, iso_year, iso_week)
    ]
    month_weeklies = [
        w for w in store.load_diary(layer="week")
        if _week_in_month(w.period or w.date, this_month)
        and (w.period or w.date) != this_week
    ]
    half_year_monthlies = [
        m for m in store.load_diary(layer="month")
        if this_month > (m.period or m.date) >= six_months_ago
    ]
    # W4 (codex): cap earlier monthlies to the 3 most recent — without this
    # the injection grows without bound as months accumulate.
    earlier_monthlies = sorted(
        [m for m in store.load_diary(layer="month")
         if (m.period or m.date) < six_months_ago],
        key=lambda m: m.period or m.date, reverse=True,
    )[:3]

    blocks: list[str] = []
    if week_dailies:
        blocks.append("## 本周（daily）\n" + _format_diary(week_dailies))
    if include_layers >= 2 and month_weeklies:
        blocks.append("## 本月除本周（weekly）\n" + _format_diary(month_weeklies))
    if include_layers >= 3 and half_year_monthlies:
        blocks.append("## 近半年除本月（monthly）\n" + _format_diary(half_year_monthlies))
    if include_layers >= 4 and earlier_monthlies:
        blocks.append("## 上半年及更早（monthly）\n" + _format_diary(earlier_monthlies))
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
