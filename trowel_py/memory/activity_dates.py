"""从会话 JSONL 字节片段提取本地活动日期及其判断依据。"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, tzinfo
from pathlib import Path
from typing import IO, Literal

DateBasis = Literal["jsonl_timestamp", "completed_at", "registered_at"]

# Claude Code 用 user/assistant 行记录会话活动，Codex 轮次日志用 user 行标记
# 活动开始；独立工具和系统事件不作为日期依据。
_TIMESTAMPED_TYPES = frozenset({"user", "assistant"})


@dataclass(frozen=True)
class ActivityDates:
    """记录会话片段对应的活动日期及其判断依据。

    Attributes:
        dates: 目标时区内按日期顺序排列且不重复的 ``YYYY-MM-DD`` 日期；
            无法确认日期时为空。
        basis: 日期来源标记；事件时间、完成时间和登记时间分别为
            ``jsonl_timestamp``、``completed_at`` 或 ``registered_at``。
            无法确认日期时也为 ``jsonl_timestamp``。
    """

    dates: tuple[str, ...]
    basis: DateBasis


def _system_local_tz() -> tzinfo | None:
    """返回当前系统使用的本地时区。"""
    return datetime.now().astimezone().tzinfo


def _parse_iso_to_date(raw: str, tz: tzinfo | None) -> str | None:
    """把 ISO 时间转换为指定时区的日期。

    没有时区的时间按系统本地时间解释。空字符串或格式错误时返回 None。

    Args:
        raw: ISO 8601 时间文本。
        tz: 结果日期使用的时区；为 None 时使用系统本地时区。

    Returns:
        ``YYYY-MM-DD`` 格式的日期；无法解析时为 None。
    """
    if not raw:
        return None
    text = raw.strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        # 完成和登记时间可能不带时区；会话事件时间通常带 Z 或显式偏移。
        dt = dt.replace(tzinfo=datetime.now().astimezone().tzinfo)
    return dt.astimezone(tz).date().isoformat()


def _date_of_line(line: bytes, tz: tzinfo | None) -> str | None:
    """从一行用户或助手事件中提取指定时区的日期。

    Args:
        line: 一行 JSONL 事件的原始字节。
        tz: 结果日期使用的时区。

    Returns:
        ``YYYY-MM-DD`` 格式的日期；事件无效、类型不匹配或没有有效时间时为 None。
    """
    try:
        obj = json.loads(line)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None
    if not isinstance(obj, dict):
        return None
    if obj.get("type") not in _TIMESTAMPED_TYPES:
        return None
    return _parse_iso_to_date(str(obj.get("timestamp", "")), tz)


def extract_activity_dates(
    jsonl_path: Path | str,
    start: int,
    end: int,
    *,
    last_completed_at: str | None = None,
    registered_at: str | None = None,
    local_tz: tzinfo | None = None,
) -> ActivityDates:
    """提取指定 JSONL 字节范围内的活动日期。

    范围内没有有效事件时间，或者已记录的文件缺失或不可读时，依次使用完成
    时间和登记时间。``jsonl_path`` 为空字符串时不使用备用时间，也不使用提炼
    任务的运行日期。

    Args:
        jsonl_path: Claude Code 会话 JSONL 或 Codex 轮次日志路径；空字符串表示
            未记录来源路径。
        start: 片段起始字节位置，包含该位置；负数按 0 处理。
        end: 片段结束字节位置，不包含该位置。
        last_completed_at: Claude Code 会话的最后完成时间或 Codex 轮次完成时间，
            事件时间不可用时优先采用。
        registered_at: Claude Code 会话或 Codex 轮次的登记时间，仅在事件时间
            和完成时间都不可用时采用。
        local_tz: 活动日期使用的时区；为 None 时使用系统本地时区。

    Returns:
        目标时区内按日期排序且不重复的日期及来源标记；无法确认日期时，
        ``dates`` 为空且 ``basis`` 为 ``jsonl_timestamp``。
    """
    tz = local_tz or _system_local_tz()
    has_path = bool(str(jsonl_path).strip())
    path = Path(jsonl_path)
    # 空字符串转换为 Path 后会指向当前目录，is_file() 可同时排除空路径和目录。
    existed = path.is_file()
    dates: set[str] = set()
    if existed and end > max(start, 0):
        try:
            with path.open("rb") as f:
                dates = _scan_range(f, start, end, tz)
        except OSError:
            dates = set()
    if dates:
        return ActivityDates(tuple(sorted(dates)), "jsonl_timestamp")
    if not has_path:
        # 未记录来源路径时无法确认活动日期，调用方据此拒绝写入非空日记。
        return ActivityDates((), "jsonl_timestamp")
    # 记录过路径但没有有效事件日期时，只使用已保存的完成或登记时间，
    # 不使用提炼任务的运行日期。
    for stamp, basis in (
        (last_completed_at, "completed_at"),
        (registered_at, "registered_at"),
    ):
        if stamp:
            day = _parse_iso_to_date(stamp, tz)
            if day:
                return ActivityDates((day,), basis)  # type: ignore[arg-type]
    return ActivityDates((), "jsonl_timestamp")


def _scan_range(f: IO[bytes], start: int, end: int, tz: tzinfo | None) -> set[str]:
    """扫描完整落在指定字节范围内的 JSONL 行。

    Args:
        f: 已打开的二进制 JSONL 文件。
        start: 起始字节位置，包含该位置；负数按 0 处理。
        end: 结束字节位置，不包含该位置。
        tz: 活动日期使用的时区。

    Returns:
        范围内有效用户和助手事件对应的日期集合。
    """
    dates: set[str] = set()
    start = max(start, 0)
    f.seek(start)
    if start > 0:
        # 前一字节不是换行符，说明 start 切在行中。
        f.seek(start - 1)
        if f.read(1) != b"\n":
            f.seek(start)
            f.readline()
        else:
            f.seek(start)
    while True:
        pos = f.tell()
        if pos >= end:
            break
        line = f.readline()
        if not line:
            break
        # 跨过 end 的行不计入当前片段，避免把水位之外的数据归给当前片段。
        if f.tell() > end:
            break
        day = _date_of_line(line, tz)
        if day:
            dates.add(day)
    return dates
