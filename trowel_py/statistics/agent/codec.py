"""提供双 runtime adapter 共用的时间、JSONL 和区间运算。"""

from __future__ import annotations

import json
from collections.abc import Iterator, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, BinaryIO

from trowel_py.statistics.agent.models import ActivityInterval, Quality
from trowel_py.statistics.window import StatisticsWindow


def parse_timestamp(value: object, *, local_naive: bool = False) -> datetime | None:
    """把 ISO 时间转换成 UTC；无效值返回 None。

    Args:
        value: JSON 或 SQLite 中的时间值。
        local_naive: 无时区字符串是否代表当前机器的当地时间。

    Returns:
        UTC 时间；格式无效时为 None。
    """

    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.astimezone() if local_naive else parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def iter_jsonl(handle: BinaryIO) -> Iterator[dict[str, Any]]:
    """逐行解码 JSONL，只返回对象记录。

    Args:
        handle: 已打开的二进制 JSONL 文件。

    Yields:
        成功解码的 JSON 对象。
    """

    for raw_line in handle:
        if not raw_line.strip():
            continue
        try:
            decoded = json.loads(raw_line)
        except (UnicodeDecodeError, json.JSONDecodeError):
            continue
        if isinstance(decoded, dict):
            yield decoded


def iter_jsonl_range(path: Path, start: int, end: int) -> Iterator[dict[str, Any]]:
    """读取 transcript 半开字节区间内的完整 JSON 对象。

    Args:
        path: JSONL 文件路径。
        start: 包含的起始字节。
        end: 不包含的结束字节。

    Yields:
        区间内成功解码的 JSON 对象。
    """

    with path.open("rb") as handle:
        handle.seek(start)
        while handle.tell() < end:
            line_start = handle.tell()
            raw_line = handle.readline()
            if not raw_line or line_start >= end:
                break
            try:
                decoded = json.loads(raw_line)
            except (UnicodeDecodeError, json.JSONDecodeError):
                continue
            if isinstance(decoded, dict):
                yield decoded


def is_in_window(value: datetime | None, window: StatisticsWindow) -> bool:
    """判断时间是否位于统计半开区间内。"""

    return value is not None and window.start <= value < window.end


def clip_interval(
    start: datetime | None,
    end: datetime | None,
    quality: Quality,
    window: StatisticsWindow,
) -> ActivityInterval | None:
    """把有效活动区间裁剪到查询时间窗。

    Args:
        start: 原始区间起点。
        end: 原始区间终点。
        quality: 原始终点质量。
        window: API 查询的半开时间窗。

    Returns:
        与查询窗相交的区间；时间缺失或不相交时为 None。
    """

    if start is None or end is None or end < start:
        return None
    clipped_start = max(start, window.start.astimezone(UTC))
    clipped_end = min(end, window.end.astimezone(UTC))
    if clipped_end <= clipped_start:
        return None
    return ActivityInterval(clipped_start, clipped_end, quality)


def mapping(value: object) -> Mapping[str, Any] | None:
    """把映射对象收窄为可读取类型，其他值返回 None。"""

    return value if isinstance(value, Mapping) else None
