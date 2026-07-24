"""组装 CC 原生系统提示词追加内容。"""

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

# 软预算只驱动 diary 降级；core 与 profile 不截断。
TOKEN_BUDGET = 30000


def build_memory_injection(
    now: str,
    root: Path | str | None = None,
    *,
    memory_enabled: bool = True,
    profile_enabled: bool = True,
) -> str:
    """按 core、profile、L0、diary、memory root 顺序组装注入。"""
    store = MemoryStore(root if root is not None else resolve_memory_root())
    sections: list[str] = []
    if memory_enabled:
        core = _render_core(store)
        if core:
            sections.append(core)
    if profile_enabled:
        profile = _render_profile(store)
        if profile:
            sections.append(profile)
    if memory_enabled:
        l0 = _render_l0(store)
        if l0:
            sections.append(l0)
    if not memory_enabled and not profile_enabled:
        return ""
    if not memory_enabled:
        return "\n\n".join(sections)
    # memory 开启时始终给出根路径与 search→read 指针。
    root_section = _render_memory_root(store.root)
    # 超预算时只逐层丢弃低优先级 diary，本周 daily 最后保留。
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
    """渲染未退休的 layer-one 规则。"""
    items = [it for it in store.load_core_items() if it.status != "retired"]
    if not items:
        return ""
    lines = ["# 铁律（强制遵守）"]
    for i, it in enumerate(items, 1):
        lines.append(f"{i}. {it.imperative}")
    return "\n".join(lines)


def _render_profile(store: MemoryStore) -> str:
    """按标准字段顺序渲染非空画像维度。"""
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
    """渲染 dictionary L0 根索引。"""
    text = store.load_dictionary_L0().strip()
    if not text:
        return ""
    return f"# 领域索引（dictionary L0，按需下钻 L1/正文）\n{text}"


def _render_memory_root(root: Path) -> str:
    """渲染 memory 根路径与检索工具用法。"""
    return (
        "# memory 根路径 + 检索\n"
        f"根：{root.resolve()}\n"
        "查笔记：memory.search(query) → memory.read(uri)\n"
        "search 结果里 requires_read=true 的笔记，看摘要不够，必须 memory.read 正文"
    )


def _render_diary(store: MemoryStore, now: str, *, include_layers: int = 4) -> str:
    """按近期层级渲染 diary；非法日期只跳过本节。"""
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
        d
        for d in store.load_diary(layer="day")
        if _in_iso_week(d.date, iso_year, iso_week)
    ]
    month_weeklies = [
        w
        for w in store.load_diary(layer="week")
        if _week_in_month(w.period or w.date, this_month)
        and (w.period or w.date) != this_week
    ]
    half_year_monthlies = [
        m
        for m in store.load_diary(layer="month")
        if this_month > (m.period or m.date) >= six_months_ago
    ]
    # 早期 monthly 只保留最近三条，避免随月份无限增长。
    earlier_monthlies = sorted(
        [
            m
            for m in store.load_diary(layer="month")
            if (m.period or m.date) < six_months_ago
        ],
        key=lambda m: m.period or m.date,
        reverse=True,
    )[:3]

    blocks: list[str] = []
    if week_dailies:
        blocks.append("## 本周（daily）\n" + _format_diary(week_dailies))
    if include_layers >= 2 and month_weeklies:
        blocks.append("## 本月除本周（weekly）\n" + _format_diary(month_weeklies))
    if include_layers >= 3 and half_year_monthlies:
        blocks.append(
            "## 近半年除本月（monthly）\n" + _format_diary(half_year_monthlies)
        )
    if include_layers >= 4 and earlier_monthlies:
        blocks.append("## 上半年及更早（monthly）\n" + _format_diary(earlier_monthlies))
    if not blocks:
        return ""
    return "# 近期日记\n\n" + "\n\n".join(blocks)


def _format_diary(entries: list[Diary]) -> str:
    """按日期倒序渲染 diary 条目。"""
    lines: list[str] = []
    for d in sorted(entries, key=lambda e: e.date, reverse=True):
        body = d.body.strip()
        lines.append(f"- [{d.date}] {body}" if body else f"- [{d.date}]")
    return "\n".join(lines)


def _estimate_tokens(text: str) -> int:
    """粗估 token：CJK 每字约 2 个，其余约每 4 字符 1 个。"""
    cjk = sum(1 for ch in text if "一" <= ch <= "鿿")
    return cjk * 2 + (len(text) - cjk) // 4
