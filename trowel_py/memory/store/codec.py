"""Memory Markdown 的 frontmatter 编解码与数据映射。"""

from __future__ import annotations

import datetime
import re
from typing import Any, cast

import yaml

from trowel_py.memory.provenance import derivation_from_dict
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
    "source_segments",
    "derivations",
    "content_hash",
)

_ILLEGAL = re.compile(r'[<>:"\\|?*\x00-\x1f]')
_WS_SLASH = re.compile(r"[\s/]+")


def _split_frontmatter(text: str) -> tuple[dict[str, Any] | None, str]:
    """拆分 Markdown 开头的 YAML frontmatter 和正文。

    仅接受各占一行的 ``---`` 起止标记。缺少有效起止标记时返回
    ``(None, 原文)``；YAML 无法解析或解析结果不是映射时返回
    ``(None, 结束标记后的正文)``。
    """

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
    """按映射插入顺序序列化 YAML，并原样追加 Markdown 正文。

    输出固定使用各占一行的 ``---`` 起止标记、块式 YAML 和 Unicode 文本。
    """

    dumped = yaml.safe_dump(
        fm, sort_keys=False, allow_unicode=True, default_flow_style=False
    )
    return f"---\n{dumped}---\n{body}"


def _coerce_meta_str(value: object) -> str:
    """把 YAML 日期转为 ISO 文本，并把其他值转为字符串。

    ``datetime.date`` 及其子类使用 ``isoformat()``；其他假值统一变为空字符串。
    """

    if isinstance(value, datetime.date):
        return value.isoformat()
    return str(value or "")


def _ordered_note_frontmatter(entry: dict[str, Any]) -> dict[str, Any]:
    """按固定顺序放置已知 Note 字段，再保留调用方的扩展字段。

    名称以 ``__`` 开头的内部字段不会进入 frontmatter；扩展字段保持输入顺序。
    """

    fm: dict[str, Any] = {k: entry[k] for k in _NOTE_KEY_ORDER if k in entry}
    for key, val in entry.items():
        if key not in fm and not key.startswith("__"):
            fm[key] = val
    return fm


def _note_from_fm(fm: dict[str, Any] | None, body: str = "") -> Note | None:
    """把 Note frontmatter 和正文转换为值对象。

    frontmatter 缺失或 ``type`` 不是 ``note`` 时返回 None。假值 ``status`` 按
    旧 ``retired`` 布尔字段恢复为 ``retired`` 或 ``active``；未知 status
    不在此处校验。数值字段通过 ``int()`` 转换，无法转换时异常直接传播；
    derivation 条目仅在 ``derivation_from_dict()`` 返回 None 时跳过，其余异常
    继续传播。
    """
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
        source_segments=tuple(fm.get("source_segments") or ()),
        derivations=tuple(
            parsed
            for value in (fm.get("derivations") or ())
            if (parsed := derivation_from_dict(value)) is not None
        ),
        content_hash=str(fm.get("content_hash", "")),
        body=body,
    )


def _diary_from_fm(fm: dict[str, Any] | None, body: str = "") -> Diary | None:
    """把 Diary frontmatter 和正文转换为值对象。

    frontmatter 缺失或 ``type`` 不是 ``diary`` 时返回 None；字段值不在此处
    校验，缺失的 layer 按 ``day`` 处理。
    """
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
    """把映射转换为 Core 条目，非映射输入返回 None。

    文本字段会调用 ``str()``，缺失的 scope 和 status 分别使用
    ``high-risk`` 与 ``seed``；枚举取值不在此处校验。
    """

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
    """判断 Note 是否同时满足支持的筛选条件。

    ``status`` 执行精确比较，``retired`` 作为布尔兼容别名映射到 status，
    ``tag`` 要求标签存在；同时提供多个条件时必须全部满足，未知键被忽略。
    """

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
    """把 Note 标题规范化为文件 stem。

    首尾空白先移除，连续空白或 ``/`` 替换为连字符，再删除
    ``<>:"\\|?*`` 和 ASCII ``0x00``–``0x1F`` 控制字符；结果为空时返回
    ``untitled``。
    """

    s = _WS_SLASH.sub("-", title.strip())
    s = _ILLEGAL.sub("", s)
    return s or "untitled"
