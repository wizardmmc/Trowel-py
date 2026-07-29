"""从 Memory 文件组装 CC 与 Codex 会话共用的系统提示词追加内容。"""

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

# 软预算只逐层丢弃 Diary；core、profile、L0 和根路径均不截断。
TOKEN_BUDGET = 30000


def build_memory_injection(
    now: str,
    root: Path | str | None = None,
    *,
    memory_enabled: bool = True,
    profile_enabled: bool = True,
) -> str:
    """按开关组装 core、profile、L0、Diary 和 Memory 根路径。

    ``profile_enabled`` 独立控制画像；``memory_enabled`` 控制其余四节，并在
    开启时保证根路径和 search→read 指针存在。完整注入超过软预算时依次丢弃
    早期 monthly、近半年 monthly、本月 weekly，并始终保留本周 daily。
    core、profile、L0 和根路径不截断；降级后仍超预算时记录告警并返回超预算
    正文。

    Args:
        now: 用于划分 Diary 时间窗口的 ISO 日期；非法值只跳过 Diary。
        root: Memory 根目录；为 ``None`` 时按配置和默认规则解析。
        memory_enabled: 是否注入 core、L0、Diary 和 Memory 根路径。
        profile_enabled: 是否注入用户画像。

    Returns:
        以空行分隔的系统提示词追加文本。``memory_enabled`` 关闭时只返回
        非空画像；画像也关闭或为空时返回空字符串。``memory_enabled`` 开启
        时至少返回 Memory 根路径章节。
    """
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
    # 即使没有可注入内容，Memory 开启时也必须暴露主动检索入口。
    root_section = _render_memory_root(store.root)
    # 从完整四层开始重算，超预算时逐层移除最低优先级的 Diary。
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
    """按存储顺序编号渲染所有未退休的 Core 条目。"""
    items = [it for it in store.load_core_items() if it.status != "retired"]
    if not items:
        return ""
    lines = ["# 铁律（强制遵守）"]
    for i, it in enumerate(items, 1):
        lines.append(f"{i}. {it.imperative}")
    return "\n".join(lines)


def _render_profile(store: MemoryStore) -> str:
    """按标准字段顺序渲染正文非空的画像维度。"""
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
    """去除首尾空白后渲染 Dictionary L0；空索引不产生章节。"""
    text = store.load_dictionary_L0().strip()
    if not text:
        return ""
    return f"# 领域索引（dictionary L0，按需下钻 L1/正文）\n{text}"


def _render_memory_root(root: Path) -> str:
    """渲染绝对 Memory 根路径和 search→read 使用约束。"""
    return (
        "# memory 根路径 + 检索\n"
        f"根：{root.resolve()}\n"
        "查笔记：memory.search(query) → memory.read(uri)\n"
        "search 结果里 requires_read=true 的笔记，看摘要不够，必须 memory.read 正文"
    )


def _render_diary(store: MemoryStore, now: str, *, include_layers: int = 4) -> str:
    """按四级时间窗口渲染 Diary。

    第一级包含 ``now`` 所在 ISO 周的 daily；第二级增加周一落在 ``now`` 所在
    月份、且周期不是本周的 weekly；第三级增加周期落在 180 天前所在月份
    （含）至本月（不含）的 monthly；第四级从更早的 monthly 中按周期字符串
    倒序选前三条。Weekly 和 Monthly 的周期优先使用 ``period``，为空时回退
    到 ``date``。``include_layers`` 小于等于 1 时仍保留第一级，大于 4 不
    增加内容。非法 ``now`` 会记录告警并返回空字符串。
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
    # 更早 monthly 只取周期字符串倒序的前三条，限制长期增长。
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
    """按 ``date`` 文本倒序渲染列表项，并清理正文首尾空白。"""
    lines: list[str] = []
    for d in sorted(entries, key=lambda e: e.date, reverse=True):
        body = d.body.strip()
        lines.append(f"- [{d.date}] {body}" if body else f"- [{d.date}]")
    return "\n".join(lines)


def _estimate_tokens(text: str) -> int:
    """基础 CJK 字符按每字 2 token 计，其余字符总数除以 4 后向下取整。"""
    cjk = sum(1 for ch in text if "一" <= ch <= "鿿")
    return cjk * 2 + (len(text) - cjk) // 4
