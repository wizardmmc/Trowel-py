"""实现 Draft JSON 的可注入兼容解析。"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, TypeVar

_DraftT = TypeVar("_DraftT")
_NoteT = TypeVar("_NoteT")
_DiaryT = TypeVar("_DiaryT")
_EpisodeItemT = TypeVar("_EpisodeItemT")


def parse_draft(
    text: str,
    *,
    loads: Callable[[str], Any],
    draft_type: Callable[..., _DraftT],
    parse_note: Callable[[dict[str, Any]], _NoteT],
    parse_diary: Callable[[dict[str, Any]], _DiaryT],
) -> _DraftT:
    """用调用方提供的解码器和构造器解析完整草稿。

    ``notes``、``diary`` 的缺失或假值按空序列处理，``reflection`` 的缺失
    或假值变为空字符串；``escalate_to_human`` 的缺失或假值变为空元组，
    其余值仅转换为元组，不清理元素。本函数负责兼容转换，不执行落盘前的
    整稿校验。解码、映射访问、子项解析和草稿构造产生的异常均不包装，直接
    向上传播。

    Args:
        text: 待解码的完整 JSON 文本。
        loads: JSON 解码函数。
        draft_type: 接收四个顶层字段的草稿构造器。
        parse_note: 单条 Note 的解析函数。
        parse_diary: 单条 Diary 的解析函数。

    Returns:
        ``draft_type`` 构造的草稿对象。

    """
    data = loads(text)
    notes = tuple(parse_note(note) for note in (data.get("notes") or []))
    diary = tuple(parse_diary(item) for item in (data.get("diary") or []))
    return draft_type(
        notes=notes,
        diary=diary,
        reflection=str(data.get("reflection") or ""),
        escalate_to_human=tuple(data.get("escalate_to_human") or ()),
    )


def parse_note(
    note: dict[str, Any],
    *,
    note_type: Callable[..., _NoteT],
) -> _NoteT:
    """按旧协议的宽松规则构造一条候选知识。

    文本字段缺失时使用各自默认值，显式 ``None`` 会由 ``str()`` 转成
    ``"None"``；``tags`` 和 ``conflicts_with`` 的假值归一为空元组，
    ``pain`` 的假值归一为 ``0``。集合真值仅转换为元组，不清理元素。

    Args:
        note: 一条 Note 字段映射。
        note_type: 接收规范 Note 字段的构造器。

    Returns:
        ``note_type`` 构造的候选知识对象。

    Raises:
        AttributeError: ``note`` 不是字段映射。
        TypeError: 真值集合字段不可迭代，或字段不支持对应类型转换。
        ValueError: ``pain`` 不能转换为整数。
        OverflowError: ``pain`` 的整数转换溢出。
    """
    return note_type(
        title=str(note.get("title", "")),
        summary=str(note.get("summary", "")),
        body=str(note.get("body", "")),
        tags=tuple(note.get("tags") or ()),
        kind=str(note.get("kind", "fact")),
        verification=str(note.get("verification", "inferred-untested")),
        verification_reason=str(note.get("verification_reason", "")),
        pain=int(note.get("pain") or 0),
        pain_reason=str(note.get("pain_reason", "")),
        conflicts_with=tuple(note.get("conflicts_with") or ()),
    )


def parse_diary(
    diary: dict[str, Any],
    *,
    diary_type: Callable[..., _DiaryT],
    str_list: Callable[[Any], tuple[str, ...]],
    parse_episode_item: Callable[[dict[str, Any]], _EpisodeItemT],
) -> _DiaryT:
    """按 ``items`` 是否非 None 解析新旧两种经历草稿。

    非 None 的 ``items`` 启用结构化模式：值必须是列表，且映射只能含
    ``date`` 和 ``items``。缺失或显式为 ``None`` 时沿用旧模式：四类文本列表
    交给 ``str_list`` 清理，``events`` 的假值归一为空字符串，其余值转换
    为字符串。

    Args:
        diary: 一天的 Diary 字段映射。
        diary_type: 接收新旧 Diary 字段的构造器。
        str_list: 旧列表字段的兼容转换函数。
        parse_episode_item: 单条结构化事件的严格解析函数。

    Returns:
        ``diary_type`` 构造的候选经历对象。

    Raises:
        TypeError: ``items`` 不是列表，或字段、结构化事件不支持对应的类型转换。
        ValueError: 结构化映射包含缺失或多余字段，或事件结构不合法。
        AttributeError: Diary 或结构化事件不是字段映射。
    """
    raw_items = diary.get("items")
    if raw_items is not None and not isinstance(raw_items, list):
        raise TypeError("diary items must be a list")
    if raw_items is not None and set(diary) != {"date", "items"}:
        raise ValueError(
            "structured episode diary keys must be exactly ['date', 'items']"
        )
    return diary_type(
        date=str(diary.get("date", "")),
        outcomes=str_list(diary.get("outcomes")),
        decisions=str_list(diary.get("decisions")),
        corrections=str_list(diary.get("corrections")),
        open_loops=str_list(diary.get("open_loops")),
        events=str(diary.get("events") or ""),
        items=tuple(parse_episode_item(item) for item in (raw_items or [])),
    )


def str_list(value: Any) -> tuple[str, ...]:
    """清理旧 Diary 的文本列表，非列表值按空列表处理。

    列表元素依次经过 ``str()`` 和首尾空白移除，结果为空的元素会被丢弃；
    其余元素保持原顺序。
    """
    if not isinstance(value, list):
        return ()
    return tuple(item for item in (str(raw).strip() for raw in value) if item)
