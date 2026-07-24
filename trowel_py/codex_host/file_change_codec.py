"""Codex fileChange payload 到前端 diff shape 的无状态转换。"""

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
