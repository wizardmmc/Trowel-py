"""把 CC 文件工具结果中预计算的 `structuredPatch` 转为 `WriteDiff`。

真实录制与上游 `FileEditOutput` 表明，history JSONL 使用 `toolUseResult`，
stream-json 使用 `tool_use_result`。history 与 translator 共用本转换器，保留
`structuredPatch` 中的真实文件行号，并统一 replay 与 live 的事件 shape。
"""

from __future__ import annotations

from typing import Any

from trowel_py.cc_host.schemas import DiffHunk, WriteDiff


def _convert_hunks(patch: Any) -> tuple[DiffHunk, ...]:
    """按列表原顺序转换 `structuredPatch` 中的 hunk。

    `patch` 不是列表时返回空元组；列表中的非字典项跳过。每个字典项的缺失或假值
    坐标补 0，`lines` 仅在原值是列表时逐项转成字符串，否则使用空元组。

    Args:
        patch: CC `structuredPatch` 的原始值。

    Returns:
        按原顺序转换的 hunk 元组。

    Raises:
        TypeError: 真值坐标字段不支持 `int()` 转换。
        ValueError: 真值坐标字段不是有效整数。
        OverflowError: 坐标字段的 `int()` 转换发生溢出。
    """
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
    """从 CC tool result 提取文件变化。

    转换后只要存在 hunk 就返回 `update`，不依赖 result 的 `type`；没有 hunk 且
    `type == "create"` 时返回空 hunk 的 `create`。其余结构正常的输入返回 `None`；
    hunk 坐标的 `int()` 转换异常直接传播。

    Args:
        tool_use_result: history 的 `toolUseResult` 或 live 消息的 `tool_use_result`。

    Returns:
        转换后的 `WriteDiff`；输入不是字典或不表示可展示的文件变化时返回 `None`。

    Raises:
        TypeError: hunk 的真值坐标字段不支持 `int()` 转换。
        ValueError: hunk 的真值坐标字段不是有效整数。
        OverflowError: hunk 坐标字段的 `int()` 转换发生溢出。
    """
    if not isinstance(tool_use_result, dict):
        return None

    hunks = _convert_hunks(tool_use_result.get("structuredPatch"))
    if hunks:
        return WriteDiff(type="update", hunks=hunks)

    if tool_use_result.get("type") == "create":
        return WriteDiff(type="create", hunks=())

    return None
