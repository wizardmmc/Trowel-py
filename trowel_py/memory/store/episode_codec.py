"""把每次提炼的 Diary 转成 Episode Markdown，并从中恢复指定日期的经历。

一个 Episode 文件对应一条原生会话。同一会话可能分多次提炼，因此正文按
每次提炼拆成带起止标记的独立来源片段。
"""

from __future__ import annotations

import hashlib
import re
from collections import OrderedDict
from typing import Any

from trowel_py.memory.draft import (
    DraftDiary,
    episode_item_text,
    parse_episode_item,
)

from .codec import _coerce_meta_str

_DIARY_FIELDS = ("outcomes", "decisions", "corrections", "open_loops")
_HEADING_FOR_KIND = {
    "outcome": "outcomes",
    "decision": "decisions",
    "correction": "corrections",
    "open_loop": "open_loops",
    "evidence": "evidence",
}
_SEG_START = re.compile(r"<!-- @segment (\S+) -->")
_SEG_END = re.compile(r"<!-- @endsegment (\S+) -->")


def _render_segment(
    segment_id: str, diary_entries: tuple[DraftDiary, ...]
) -> tuple[str, str, list[str], str]:
    """把一次会话提炼得到的 Diary 写成带起止标记的来源片段。

    同一会话可能被增量提炼多次，每次提炼结果在 Episode 正文中占一个来源
    片段，并用 ``segment_id`` 区分。空输入会写入固定的 ``empty_reason``
    占位。内容哈希只覆盖起止标记内的 Markdown，取 SHA-256 前 16 个十六进制
    字符；``segment_id`` 会直接写入标记，调用方必须保证它不含空白字符。

    Returns:
        来源片段 Markdown、内容哈希、按渲染顺序收集的日期和空输入原因；
        非空输入的原因是空字符串。
    """

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
    """把一天的经历渲染为以二级日期标题开头的 Markdown。

    ``items`` 非空时按 outcome、decision、correction、open_loop、evidence
    分组，组内保持输入顺序。否则按四个旧结构化字段渲染；只要其中一组非空，
    ``events`` 就被忽略。四组也为空时保留自由文本，纯空白则只写日期标题。
    """

    if d.items:
        structured_sections: list[str] = []
        for kind, heading in _HEADING_FOR_KIND.items():
            items = [item for item in d.items if item.kind == kind]
            if not items:
                continue
            bullets = "\n".join(_render_episode_item(item) for item in items)
            structured_sections.append(f"#### {heading}\n{bullets}")
        return f"## {d.date}\n\n" + "\n\n".join(structured_sections) + "\n"
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
    """去掉首尾空白，并把连续空白折叠为单个空格。"""

    return " ".join(text.split())


def _render_episode_item(item: Any) -> str:
    """把一条结构化 Episode 项渲染为单行列表项。

    可读正文中的空白会折叠，非空 status 会追加为字段后缀。函数假定对象符合
    Episode item 接口，不在此处校验。
    """
    text = episode_item_text(item)
    status = getattr(item, "status", "")
    status_text = f"; status: {status}" if status else ""
    return f"- {_single_line(text)}{status_text}"


def _entry_from_episode_meta(meta: dict[str, Any], date: str) -> DraftDiary | None:
    """从一次提炼的元数据中恢复指定日期的结构化经历。

    当前写入的 item 不含 ``source_refs``，可以直接解析；早期记录仍带有这个
    已停用的行号字段，读取时先忽略它，使已有 Episode 继续可读。无法识别的
    结构格式返回 None。其他日期和非映射记录会被忽略；目标日期的 item 结构
    错误或解析失败会放弃整次恢复，让调用方回退到 Markdown。目标日期没有
    item 时，仅当 ``activity_dates`` 包含该日期才返回空 ``DraftDiary``。
    """
    version = meta.get("episode_schema_version")
    if version not in {2, 3}:
        return None
    items = []
    for record in meta.get("episode_items") or []:
        if not isinstance(record, dict) or record.get("date") != date:
            continue
        raw_item = record.get("item")
        if not isinstance(raw_item, dict):
            return None
        item_payload = dict(raw_item)
        if version == 2:
            item_payload.pop("source_refs", None)
        try:
            items.append(parse_episode_item(item_payload))
        except (TypeError, ValueError):
            return None
    dates = {_coerce_meta_str(value) for value in meta.get("activity_dates") or []}
    if not items and date not in dates:
        return None
    return DraftDiary(date=date, items=tuple(items))


def _parse_structured_block(block_text: str, date: str) -> DraftDiary:
    """解析旧格式日期块中的四类结构化列表。

    只识别行首的 outcomes、decisions、corrections、open_loops 四种四级标题，
    并收集其后以 ``- `` 开头的非空列表项。没有四级标题，或没有解析出任何
    已知列表项时，把整个块去除首尾空白后放入 ``events``；一旦解析出已知项，
    未知 section 和普通文本会被忽略。
    """

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
    """按提炼片段 ID 首次出现的顺序拆出 Episode 中的各个来源片段。

    搜索会跳过其他 ID 的结束标记，直到找到当前 ID；始终找不到匹配结束标记
    的起点会被丢弃。返回的块包含起止标记并补一个换行，标记之外的文本忽略。
    重复 ID 保留首次出现的位置，但内容由最后出现的块覆盖。
    """

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
    """按旧 Episode 顶层日期元数据判断是否覆盖目标日期。

    非空 ``activity_dates`` 优先并逐项转为文本；字段缺失或为空时回退比较
    ``review_date``。字段容器类型不在此处校验。
    """

    ad = fm.get("activity_dates")
    if ad:
        return date in [_coerce_meta_str(d) for d in ad]
    return _coerce_meta_str(fm.get("review_date")) == date


def _segment_entry_for_date(
    block: str, date: str, seg_meta: dict[str, Any]
) -> str | None:
    """确认一次提炼覆盖目标日期后，提取该日期标题下的正文。

    非空 ``activity_dates`` 不包含目标日期时直接返回 None；字段缺失或为空时，
    目标日期必须出现在块的二级标题列表中。通过任一门禁后仍要求块中存在该日期
    标题和非空正文。
    """

    ad = seg_meta.get("activity_dates")
    if ad:
        ad_str = [_coerce_meta_str(d) for d in ad]
        if date not in ad_str:
            return None
    elif date not in _h2_headings(block):
        return None
    return _extract_h2_block(block, date)


def _h2_headings(block: str) -> list[str]:
    """按顺序返回从行首 ``## `` 标题提取的文本。

    行尾空白会被移除，但前缀后的额外空格会保留；带缩进的标题不识别。
    """

    return [ln.strip()[3:] for ln in block.splitlines() if ln.startswith("## ")]


def _extract_h2_block(block: str, date: str) -> str | None:
    """提取首个目标日期标题后的非空正文。

    查找目标标题时忽略整行首尾空白，因此允许标题缩进；开始捕获后，只有位于
    行首的下一个二级标题或当前来源片段的结束标记才会终止。标题不存在或
    去除首尾空白后正文为空时返回 None。
    """

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
