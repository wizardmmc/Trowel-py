"""管理 Daily 派生摘要的缓存复用、失败回退、重建扫描和写入。"""

from __future__ import annotations

import logging
import re
from datetime import datetime
from pathlib import Path

from trowel_py.llm.client import LLMProvider
from trowel_py.memory.store import MemoryStore, _split_frontmatter

from .daily_generation import (
    _DAILY_BUDGET,
    _DAILY_GENERATION_VERSION,
    _dedup_items,
    _generate_items,
    _render_daily_body,
    _render_sources_block,
    _required_sections,
    _select_within_budget,
    _source_aliases,
    _source_hash,
)

logger = logging.getLogger("trowel_py.memory.compress")


def _existing_daily_usable(root_path: Path, date_str: str, shash: str) -> bool:
    """判断已有成功 Daily 能否在当前来源的重新生成失败后保留。

    Args:
        root_path: memory 数据目录。
        date_str: 要检查的 Daily 日期，格式为 ``YYYY-MM-DD``。
        shash: 当前 Episode 来源的内容哈希。
    """
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
    """判断已有 Daily 是否为当前来源和生成版本的可复用成功摘要。

    Args:
        root_path: memory 数据目录。
        date_str: 要检查的 Daily 日期，格式为 ``YYYY-MM-DD``。
        shash: 当前 Episode 来源的内容哈希。
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
    store: MemoryStore,
    date_str: str,
    body: str,
    source_segments: list[str],
    shash: str,
    status: str,
) -> None:
    """将 Daily 正文和生成元数据写入 Diary。

    Args:
        store: 负责写入 memory 数据目录的存储对象。
        date_str: Daily 对应的日期，格式为 ``YYYY-MM-DD``。
        body: 要写入的 Markdown 正文。
        source_segments: 正文所依据的 Episode segment ID。
        shash: 全部结构化来源的内容摘要，用于判断缓存是否过期。
        status: 本次产物的生成状态，例如 ``"ok"`` 或 ``"fallback"``。
    """
    store.write_diary(
        {
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
        }
    )


def _write_fallback_body(
    store: MemoryStore, date_str: str, source_segments: list[str], shash: str
) -> None:
    """写入指向 Episode 来源的短提示，不把未压缩经历冒充 Daily 摘要。

    Args:
        store: 负责写入 memory 数据目录的存储对象。
        date_str: fallback 对应的日期，格式为 ``YYYY-MM-DD``。
        source_segments: fallback 要列出的 Episode segment ID。
        shash: 当前 Episode 来源的内容哈希，写入元数据供后续重建判断。
    """
    seg_list = "\n".join(f"- {s}" for s in source_segments) or "- (无来源)"
    body = (
        f"# {date_str}\n\n"
        f"当日未生成可用摘要（fallback）。原始结构化经历保存在以下 episode segment，"
        f"下次 review/tidy 会重试：\n{seg_list}\n"
    )
    _write_daily(store, date_str, body, source_segments, shash, "fallback")


def compress_daily(
    root: Path | str,
    date_str: str,
    provider: LLMProvider,
    *,
    force: bool = False,
) -> str:
    """从指定日期的 Episode 来源生成并写入 Daily 摘要。

    现有 Daily 为成功状态、来源哈希与当前来源一致、生成版本匹配且正文未超
    预算时，默认不再调用模型。生成失败时优先保留同源且未超预算的成功旧摘要，
    否则写入只列出来源的 fallback。

    Args:
        root: memory 数据目录。
        date_str: 要生成摘要的日期，格式为 ``YYYY-MM-DD``。
        provider: 生成结构化摘要条目的模型客户端。
        force: 是否跳过缓存命中检查并重新生成；失败时仍可保留可用旧摘要。

    Returns:
        当天存在 Episode 来源时返回 ``date_str``，无论命中缓存、保留旧摘要还是
        写入新摘要或 fallback；没有来源时返回空字符串，且不调用模型或写文件。
    """
    root_path = Path(root)
    store = MemoryStore(root_path)
    sources = store.project_daily_sources(date_str)
    if not sources:
        return ""
    source_segments = sorted({seg_id for seg_id, _reg, _entry in sources})
    shash = _source_hash(sources)
    if not force and _existing_daily_ok(root_path, date_str, shash):
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
        # 预算选择只删除完整条目，并为每个已有 section 保留一项；
        # 剩余正文仍超限时只能写 fallback。
        logger.warning(
            "daily %s: body still %d chars after whole-bullet selection; fallback",
            date_str,
            len(body),
        )
    # 只要旧摘要来源未变且仍可用，重新生成失败就不覆盖它。
    if preserve_on_failure:
        logger.warning(
            "daily %s: regeneration failed; preserving previous usable daily",
            date_str,
        )
        return date_str
    _write_fallback_body(store, date_str, source_segments, shash)
    return date_str


def write_fallback_daily(root: Path | str, date_str: str) -> str:
    """为指定日期写入可追溯的 Daily fallback。

    当天存在 Episode 来源时会覆盖该日期已有的 Daily；没有来源时不写文件。

    Args:
        root: memory 数据目录。
        date_str: fallback 对应的日期，格式为 ``YYYY-MM-DD``。

    Returns:
        写入成功时返回 ``date_str``；当天没有 Episode 来源时返回空字符串且不写文件。
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
    """返回存在 Episode 来源但需要重建 Daily 的日期。

    候选日期从 Episode 顶层的 ``activity_dates``、``review_date``、各 segment 的
    ``activity_dates``，以及旧版 Episode 正文的日期标题中收集。对应 Daily 缺失、
    状态不是 ``"ok"``、来源哈希变化、生成版本过期或正文超出预算时，日期才会返回。

    Args:
        root: memory 数据目录。

    Returns:
        按日期升序排列的 ``YYYY-MM-DD`` 字符串。
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
