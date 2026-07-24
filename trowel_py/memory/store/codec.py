"""Memory Markdown 的 frontmatter 编解码与数据映射。"""

from __future__ import annotations

import datetime
import re
from typing import Any, cast

import yaml

from trowel_py.memory.types import CoreItem, Diary, Note, NoteStatus

_NOTE_KEY_ORDER = (
    "type",
    "title",
    "kind",
    "tags",
    "summary",
    "created",
    "updated",
    "verification",
    "verification_reason",
    "pain",
    "pain_reason",
    "conflicts_with",
    "memory_id",
    "status",
    "supersedes",
    "superseded_by",
    "valid_from",
    "last_verified_at",
    "refs",
    "read_sessions",
    "helpful_refs",
    "harmful_refs",
    "last_ref",
    "trigger",
    "do_not_use_when",
    "sources",
    "source_sessions",
    "content_hash",
)

_ILLEGAL = re.compile(r'[<>:"\\|?*\x00-\x1f]')
_WS_SLASH = re.compile(r"[\s/]+")


def _split_frontmatter(text: str) -> tuple[dict[str, Any] | None, str]:

    if not text.startswith("---"):
        return None, text
    lines = text.splitlines(keepends=True)
    if not lines or lines[0].strip() != "---":
        return None, text
    fm_lines: list[str] = []
    i = 1
    while i < len(lines) and lines[i].strip() != "---":
        fm_lines.append(lines[i])
        i += 1
    if i >= len(lines):
        return None, text
    try:
        fm = yaml.safe_load("".join(fm_lines))
    except yaml.YAMLError:
        return None, "".join(lines[i + 1 :])
    body = "".join(lines[i + 1 :])
    return (fm if isinstance(fm, dict) else None), body


def _dump_frontmatter(fm: dict[str, Any], body: str) -> str:

    dumped = yaml.safe_dump(
        fm, sort_keys=False, allow_unicode=True, default_flow_style=False
    )
    return f"---\n{dumped}---\n{body}"


def _coerce_meta_str(value: object) -> str:
    """把 YAML 自动解析的日期恢复为稳定 ISO 字符串。"""

    if isinstance(value, datetime.date):
        return value.isoformat()
    return str(value or "")


def _safe_snapshot_name(value: object) -> str:

    text = re.sub(r"[\\/\x00]+", "_", _coerce_meta_str(value)).strip().lstrip(".")
    return text or "unknown"


def _ordered_note_frontmatter(entry: dict[str, Any]) -> dict[str, Any]:

    fm: dict[str, Any] = {k: entry[k] for k in _NOTE_KEY_ORDER if k in entry}
    for key, val in entry.items():
        if key not in fm and not key.startswith("__"):
            fm[key] = val
    return fm


def _note_from_fm(fm: dict[str, Any] | None, body: str = "") -> Note | None:
    """缺失 status 的旧 note 继续按 retired 字段解释生命周期。"""
    if not fm or fm.get("type") != "note":
        return None

    status = fm.get("status")
    if not status:
        status = "retired" if fm.get("retired") else "active"
    return Note(
        type="note",
        title=str(fm.get("title", "")),
        tags=tuple(fm.get("tags") or ()),
        kind=fm.get("kind", "fact"),
        summary=str(fm.get("summary", "")),
        created=str(fm.get("created", "")),
        updated=str(fm.get("updated", "")),
        verification=fm.get("verification", "inferred-untested"),
        verification_reason=str(fm.get("verification_reason", "")),
        pain=int(fm.get("pain") or 0),
        pain_reason=str(fm.get("pain_reason", "")),
        conflicts_with=tuple(fm.get("conflicts_with") or ()),
        memory_id=str(fm.get("memory_id", "")),
        status=cast("NoteStatus", status),
        supersedes=tuple(fm.get("supersedes") or ()),
        superseded_by=str(fm.get("superseded_by", "")),
        valid_from=str(fm.get("valid_from", "")),
        last_verified_at=str(fm.get("last_verified_at", "")),
        refs=int(fm.get("refs") or 0),
        read_sessions=int(fm.get("read_sessions") or 0),
        helpful_refs=int(fm.get("helpful_refs") or 0),
        harmful_refs=int(fm.get("harmful_refs") or 0),
        last_ref=str(fm.get("last_ref", "")),
        trigger=str(fm.get("trigger", "")),
        do_not_use_when=str(fm.get("do_not_use_when", "")),
        sources=tuple(fm.get("sources") or ()),
        source_sessions=tuple(fm.get("source_sessions") or ()),
        content_hash=str(fm.get("content_hash", "")),
        body=body,
    )


def _diary_from_fm(fm: dict[str, Any] | None, body: str = "") -> Diary | None:
    if not fm or fm.get("type") != "diary":
        return None
    return Diary(
        type="diary",
        date=str(fm.get("date", "")),
        layer=fm.get("layer", "day"),
        period=str(fm.get("period", "")),
        promoted_knowledge=tuple(fm.get("promoted_knowledge") or ()),
        body=body,
    )


def _core_item_from_dict(d: object) -> CoreItem | None:

    if not isinstance(d, dict):
        return None
    return CoreItem(
        id=str(d.get("id", "")),
        imperative=str(d.get("imperative", "")),
        scope=d.get("scope", "high-risk"),
        status=d.get("status", "seed"),
        source=str(d.get("source", "")),
    )


def _matches(note: Note, filter: dict[str, Any]) -> bool:
    """保留 retired 过滤别名，供旧调用方跨迁移期使用。"""

    if "status" in filter and note.status != filter["status"]:
        return False
    if "retired" in filter:
        want_retired = bool(filter["retired"])
        is_retired = note.status == "retired"
        if want_retired != is_retired:
            return False
    tag = filter.get("tag")
    if tag is not None and tag not in note.tags:
        return False
    return True


def _slugify(title: str) -> str:

    s = _WS_SLASH.sub("-", title.strip())
    s = _ILLEGAL.sub("", s)
    return s or "untitled"
