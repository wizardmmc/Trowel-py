"""把 CC tool result 中预计算的 structured patch 转为 ``WriteDiff``。

真实录制与上游 `FileEditOutput` 表明，jsonl 使用 ``toolUseResult``，stream-json
使用 ``tool_use_result``，二者都携带按真实文件行号计算的 ``structuredPatch``。
history 与 translator 共用本转换器，保证 replay/live shape 一致。
"""

from __future__ import annotations

from typing import Any

from trowel_py.schemas.cc_host import DiffHunk, WriteDiff


def _convert_hunks(patch: Any) -> tuple[DiffHunk, ...]:
    """按上游 hunkSchema 转换；非 list 返回空 tuple，非 dict hunk 跳过。"""
    if not isinstance(patch, list):
        return ()
    out: list[DiffHunk] = []
    for h in patch:
        if not isinstance(h, dict):
            continue
        raw_lines = h.get("lines", [])
        lines = (
            tuple(str(ln) for ln in raw_lines) if isinstance(raw_lines, list) else ()
        )
        out.append(
            DiffHunk(
                oldStart=int(h.get("oldStart", 0) or 0),
                oldLines=int(h.get("oldLines", 0) or 0),
                newStart=int(h.get("newStart", 0) or 0),
                newLines=int(h.get("newLines", 0) or 0),
                lines=lines,
            )
        )
    return tuple(out)


def write_diff_from_cc_result(tool_use_result: Any) -> WriteDiff | None:
    """有效 hunks 优先映射 update；空 patch 的 create 映射 create；其余返回 None。"""
    if not isinstance(tool_use_result, dict):
        return None

    hunks = _convert_hunks(tool_use_result.get("structuredPatch"))
    if hunks:
        return WriteDiff(type="update", hunks=hunks)

    if tool_use_result.get("type") == "create":
        return WriteDiff(type="create", hunks=())

    return None
