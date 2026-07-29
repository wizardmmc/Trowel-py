"""将 Codex ``fileChange`` 条目转换为前端使用的文件变更字段。

新增和删除条目携带完整文件内容，更新条目携带 unified diff；本模块将两种输入统一
为包含操作类型和变更区块的结构，不读取文件系统。
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from typing import Any

HUNK_HEADER = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")


def parse_unified_diff(
    patch: str,
    *,
    hunk_header: re.Pattern[str],
) -> tuple[dict[str, Any], ...]:
    """提取 unified diff 中的区块范围和带标记内容行。

    Args:
        patch: 包含一个或多个 hunk 的 unified diff 文本。
        hunk_header: 用于提取新旧文件起始行和行数的正则表达式。

    Returns:
        按原顺序排列的前端变更区块；没有匹配的 hunk 时为空元组。文件头及
        ``\\ No newline at end of file`` 等非内容行不会进入结果。
    """

    hunks: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    lines_buf: list[str] = []
    for line in patch.splitlines():
        match = hunk_header.match(line)
        if match:
            if current is not None:
                current["lines"] = tuple(lines_buf)
                hunks.append(current)
            current = {
                "oldStart": int(match.group(1)),
                "oldLines": int(match.group(2)) if match.group(2) is not None else 1,
                "newStart": int(match.group(3)),
                "newLines": int(match.group(4)) if match.group(4) is not None else 1,
            }
            lines_buf = []
            continue
        if current is None:
            continue
        if line.startswith((" ", "+", "-")):
            lines_buf.append(line)
    if current is not None:
        current["lines"] = tuple(lines_buf)
        hunks.append(current)
    return tuple(hunks)


def full_file_hunk(text: str, marker: str) -> tuple[dict[str, Any], ...]:
    """将新增或删除文件的完整内容转换为单个变更区块。

    Args:
        text: 文件完整内容。
        marker: 新增使用 ``+``，删除使用 ``-``。

    Returns:
        单个新增或删除区块；空文件返回空元组。
    """

    lines = text.splitlines()
    if not lines:
        return ()
    marked = tuple(f"{marker}{line}" for line in lines)
    count = len(lines)
    if marker == "+":
        return (
            {
                "oldStart": 0,
                "oldLines": 0,
                "newStart": 1,
                "newLines": count,
                "lines": marked,
            },
        )
    return (
        {
            "oldStart": 1,
            "oldLines": count,
            "newStart": 0,
            "newLines": 0,
            "lines": marked,
        },
    )


def file_change_write_diff(
    kind_type: Any,
    diff: Any,
    *,
    add_type: Any,
    delete_type: Any,
    update_type: Any,
    full_file_hunk_fn: Callable[[str, str], tuple[dict[str, Any], ...]],
    parse_unified_diff_fn: Callable[[str], tuple[dict[str, Any], ...]],
    protocol_violation_type: Callable[..., Exception],
) -> dict[str, Any]:
    """按 Codex 文件操作类型选择完整文件或 unified diff 转换。

    Args:
        kind_type: ``fileChange.kind.type`` 的原始值。
        diff: Codex 提供的完整文件内容或 unified diff。
        add_type: 表示新增文件的协议值。
        delete_type: 表示删除文件的协议值。
        update_type: 表示更新文件的协议值。
        full_file_hunk_fn: 完整文件内容转换函数。
        parse_unified_diff_fn: unified diff 转换函数。
        protocol_violation_type: 操作类型不是新增、删除或更新时使用的异常类型。

    Returns:
        包含前端操作类型和变更区块的 diff 对象。
    """

    text = str(diff or "")
    if kind_type == add_type:
        return {"type": "create", "hunks": full_file_hunk_fn(text, "+")}
    if kind_type == delete_type:
        return {"type": "delete", "hunks": full_file_hunk_fn(text, "-")}
    if kind_type == update_type:
        return {"type": "update", "hunks": parse_unified_diff_fn(text)}
    raise protocol_violation_type(
        f"fileChange write_diff: unexpected kind type {kind_type!r}",
        payload={"kind_type": kind_type},
    )


def file_change_to_change(
    change: Mapping[str, Any],
    method: str,
    *,
    add_type: Any,
    delete_type: Any,
    update_type: Any,
    mapping_type: Any,
    as_str: Callable[[Any], str],
    write_diff_fn: Callable[[Any, Any], dict[str, Any]],
    protocol_violation_type: Callable[..., Exception],
) -> dict[str, Any]:
    """读取 Codex 文件变更条目并生成路径、操作类型和 diff 字段。

    更新条目仅在 ``kind.movePath`` 非空时视为重命名；不兼容读取
    ``kind.move_path``。

    Args:
        change: 单个 ``fileChange.changes`` 条目。
        method: 产生该条目的通知方法名，用于协议错误诊断。
        add_type: 表示新增文件的协议值。
        delete_type: 表示删除文件的协议值。
        update_type: 表示更新文件的协议值。
        mapping_type: 用于校验 ``kind`` 对象的映射类型。
        as_str: 将路径字段转换为字符串的函数。
        write_diff_fn: 将操作类型和原始 diff 转换为前端字段的函数。
        protocol_violation_type: ``kind`` 或操作类型不符合协议时使用的异常类型。

    Returns:
        前端文件变更字段；缺失的 ``path`` 转为空字符串。
    """

    kind = change.get("kind")
    if not isinstance(kind, mapping_type):
        raise protocol_violation_type(
            f"notification {method!r} fileChange change.kind is not an object",
            payload=dict(change),
        )
    kind_type = kind.get("type")
    move_path_raw = kind.get("movePath")
    move_path = str(move_path_raw) if move_path_raw else None
    if kind_type == add_type:
        change_kind = "add"
    elif kind_type == delete_type:
        change_kind = "delete"
    elif kind_type == update_type:
        change_kind = "rename" if move_path else "modify"
    else:
        raise protocol_violation_type(
            f"notification {method!r} fileChange change.kind.type has "
            f"unexpected value {kind_type!r}",
            payload=dict(change),
        )
    raw_path = change.get("path")
    return {
        "path": as_str(raw_path) if raw_path is not None else "",
        "change_kind": change_kind,
        "move_path": move_path,
        "write_diff": write_diff_fn(kind_type, change.get("diff")),
    }
