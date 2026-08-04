"""向 Statistics 领域提供不含正文和身份标识的 Memory 只读快照。"""

from __future__ import annotations

from datetime import date, datetime, time, tzinfo
from pathlib import Path
from typing import Any

from trowel_py.memory.access_log import AccessRecord, OutcomeRecord
from trowel_py.memory.dictionary_state import load_state
from trowel_py.memory.judgements import JudgementReport
from trowel_py.memory.north_star import (
    compute_north_star_from_notes,
    memory_usage_metrics_from_notes,
)
from trowel_py.memory.store import MemoryStore
from trowel_py.memory.types import Note, NoteId


def read_memory_statistics(
    root: Path | str,
    *,
    window_start: datetime,
    window_end: datetime,
    local_tz: tzinfo | None,
    strict_read_only: bool = False,
    notes_with_id: list[tuple[NoteId, Note]] | None = None,
    access_records: list[AccessRecord] | None = None,
    outcome_records: list[OutcomeRecord] | None = None,
    judgement_reports: list[JudgementReport] | None = None,
) -> dict[str, Any]:
    """读取查询窗内使用事实和当前 Memory 资产快照。

    搜索、读取、效果和召回由 ``north_star`` 继续计算；本门面只补充当前资产、
    Dictionary 状态和各来源的样本范围。返回值不包含 Note 正文、查询文本、
    工作目录或任何会话 ID。

    Args:
        root: Memory 根目录。
        window_start: 查询包含的开始时刻。
        window_end: 查询不包含的结束时刻。
        local_tz: 活动日期使用的时区；为 None 时使用系统本地时区。
        strict_read_only: 是否禁止会话数据库迁移并使用只读归因快照。
        notes_with_id: 调用方已加载的 Note 快照；省略时由本门面读取。
        access_records: 调用方已加载的访问日志快照；省略时由领域层读取。
        outcome_records: 调用方已加载的反馈日志快照；省略时由领域层读取。
        judgement_reports: 调用方已加载的判效报告快照；省略时由领域层读取。

    Returns:
        包含 ``usage``、``assets`` 和 ``sources`` 的只读快照。
    """
    root_path = Path(root)
    note_rows = (
        MemoryStore(root_path).load_notes_with_id()
        if notes_with_id is None
        else notes_with_id
    )
    usage = memory_usage_metrics_from_notes(
        root_path,
        note_rows,
        local_tz=local_tz,
        window_start=window_start,
        window_end=window_end,
        strict_read_only=strict_read_only,
        access_records=access_records,
        outcome_records=outcome_records,
        judgement_reports=judgement_reports,
    )
    health = compute_north_star_from_notes(
        root_path,
        note_rows,
        access_records=access_records,
        outcome_records=outcome_records,
    )
    dictionary = load_state(root_path)
    notes = [note for _stem, note in note_rows]
    sources = dict(usage["sources"])
    sources["notes"] = _notes_source(notes, local_tz)
    sources["dictionary"] = _dictionary_source(dictionary, local_tz)
    return {
        "usage": usage,
        "assets": {
            **health,
            "dictionary_status": dictionary.status,
            "dictionary_updated_at": dictionary.last_failure_at
            if dictionary.status == "stale"
            else dictionary.last_success_at,
        },
        "sources": sources,
    }


def _notes_source(notes: list[Any], local_tz: tzinfo | None) -> dict[str, Any]:
    """汇总 Note 日期，不读取或返回正文。"""
    parsed = sorted(
        {
            value
            for note in notes
            for raw in (note.updated, note.created)
            if (value := _safe_date(raw)) is not None
        }
    )
    unknown = sum(
        1 for note in notes if _safe_date(note.updated or note.created) is None
    )
    return {
        "updated_at": _date_timestamp(parsed[-1], local_tz) if parsed else None,
        "sample_start": _date_timestamp(parsed[0], local_tz) if parsed else None,
        "sample_end": _date_timestamp(parsed[-1], local_tz) if parsed else None,
        "sample_size": len(notes),
        "unknown_time_records": unknown,
        "quality": "partial"
        if unknown
        else ("reliable" if notes else "unavailable"),
    }


def _dictionary_source(state: Any, local_tz: tzinfo | None) -> dict[str, Any]:
    """把 Dictionary 状态转换为统计来源摘要。"""
    updated_at = (
        state.last_failure_at if state.status == "stale" else state.last_success_at
    )
    parsed = _safe_datetime(updated_at, local_tz)
    return {
        "updated_at": parsed.isoformat() if parsed is not None else None,
        "sample_start": parsed.isoformat() if parsed is not None else None,
        "sample_end": parsed.isoformat() if parsed is not None else None,
        "sample_size": 1 if state.status != "missing" else 0,
        "unknown_time_records": 0 if parsed is not None else int(state.status != "missing"),
        "quality": "reliable"
        if state.status == "consistent" and parsed is not None
        else ("partial" if state.status == "stale" else "unavailable"),
        "status": state.status,
    }


def _safe_date(raw: str) -> date | None:
    """解析 Note 日期，空值和旧式异常值返回 None。"""
    try:
        return date.fromisoformat(raw)
    except ValueError:
        return None


def _safe_datetime(raw: str | None, local_tz: tzinfo | None) -> datetime | None:
    """解析可带时区的状态时间，无时区值按本地时区解释。"""
    if not raw:
        return None
    text = raw[:-1] + "+00:00" if raw.endswith("Z") else raw
    try:
        value = datetime.fromisoformat(text)
    except ValueError:
        return None
    return value if value.tzinfo is not None else value.replace(tzinfo=local_tz)


def _date_timestamp(value: date, local_tz: tzinfo | None) -> str:
    """把只有日期的资产事实表示为当地零点时间。"""
    return datetime.combine(value, time.min, tzinfo=local_tz).isoformat()
