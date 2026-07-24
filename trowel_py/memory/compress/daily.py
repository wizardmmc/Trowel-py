"""Daily 摘要的缓存、回退、重建扫描与落盘。"""

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
    """判断旧摘要能否在本次重新生成失败后继续保留。"""
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
    """仅复用来源、预算和生成版本都匹配的成功摘要。"""
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
    """只写可追溯来源的短提示，不把未压缩经历伪装成摘要。"""
    seg_list = "\n".join(f"- {s}" for s in source_segments) or "- (无来源)"
    body = (
        f"# {date_str}\n\n"
        f"当日未生成可用摘要（fallback）。原始结构化经历保存在以下 episode segment，"
        f"下次 review/tidy 会重试：\n{seg_list}\n"
    )
    _write_daily(store, date_str, body, source_segments, shash, "fallback")


def compress_daily(
    root: Path | str, date_str: str, provider: LLMProvider
) -> str:
    """生成一天的结构化摘要；没有 episode 时不伪造空日记。"""
    root_path = Path(root)
    store = MemoryStore(root_path)
    sources = store.project_daily_sources(date_str)
    if not sources:
        return ""
    source_segments = sorted({seg_id for seg_id, _reg, _entry in sources})
    shash = _source_hash(sources)
    if _existing_daily_ok(root_path, date_str, shash):
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
        # 单条摘要仍超限时不能截断句子，只能降级为 fallback。
        logger.warning(
            "daily %s: body still %d chars after whole-bullet selection; fallback",
            date_str,
            len(body),
        )
    # 生成版本升级失败不能覆盖来源未变且仍可用的旧摘要。
    if preserve_on_failure:
        logger.warning(
            "daily %s: regeneration failed; preserving previous usable daily",
            date_str,
        )
        return date_str
    _write_fallback_body(store, date_str, source_segments, shash)
    return date_str


def write_fallback_daily(root: Path | str, date_str: str) -> str:
    """无 provider 时写可追溯的 fallback；没有 episode 时不落盘。"""
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
    """找出缺失、失败、过期或来源变化的 daily 派生缓存。"""
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
