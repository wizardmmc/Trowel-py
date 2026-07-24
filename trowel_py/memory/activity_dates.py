"""从 CC JSONL 字节片段提取本地活动日期与证据来源。"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, tzinfo
from pathlib import Path
from typing import IO, Literal

DateBasis = Literal["jsonl_timestamp", "completed_at", "registered_at"]

# tool 结果嵌在 user/assistant 消息中，其他时间戳行会重复计算同一次交互。
_TIMESTAMPED_TYPES = frozenset({"user", "assistant"})


@dataclass(frozen=True)
class ActivityDates:
    """记录片段涉及的日期，以及日期来自事件还是 fallback。"""

    dates: tuple[str, ...]
    basis: DateBasis


def _system_local_tz() -> tzinfo | None:
    return datetime.now().astimezone().tzinfo


def _parse_iso_to_date(raw: str, tz: tzinfo | None) -> str | None:
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
        # fallback 水位来自本地 wall clock；CC JSONL 时间戳带 Z，不走此分支。
        dt = dt.replace(tzinfo=datetime.now().astimezone().tzinfo)
    return dt.astimezone(tz).date().isoformat()


def _date_of_line(line: bytes, tz: tzinfo | None) -> str | None:
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
    """扫描 `[start, end)`；无事件日期时只使用已记录水位，不猜测运行日。"""
    tz = local_tz or _system_local_tz()
    has_path = bool(str(jsonl_path).strip())
    path = Path(jsonl_path)
    # Path("") 是当前目录；is_file() 同时排除空路径和目录。
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
        # 未记录 JSONL 路径时没有归因事实，调用方据此拒绝非空 diary。
        return ActivityDates((), "jsonl_timestamp")
    # 路径存在记录但无事件日期时，只按持久水位降级，绝不使用 review 运行日。
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
    """扫描 `[start, end)`，丢弃边界处不完整的行。"""
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
        # 跨过 end 的行不计入当前片段，避免读取水位之外的数据。
        if f.tell() > end:
            break
        day = _date_of_line(line, tz)
        if day:
            dates.add(day)
    return dates
