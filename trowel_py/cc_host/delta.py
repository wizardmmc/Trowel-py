"""累积 Claude Code stream_event 的工具输入分片，并在内容块闭合时返回参数。

上游 Anthropic 协议用 ``content_block_start`` 声明内容块，通过
``input_json_delta.partial_json`` 分段传递参数，直到 ``content_block_stop`` 才能
得到完整 JSON。text 和 thinking 增量由 translator 直接处理，不进入这里。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class ToolBlockResult:
    """保存已闭合 tool_use 内容块的工具身份和完整参数。

    Attributes:
        tool_use_id: Claude Code 为本次工具调用分配的 ID。
        tool_name: Claude Code 报告的工具名称；开始事件未提供名称时为空字符串。
        input: 由参数分片解析出的工具参数；没有分片或 JSON 无法解析时为空字典。
    """

    tool_use_id: str
    tool_name: str
    input: dict[str, Any]


@dataclass
class _Block:
    """记录 assistant 消息中一个尚未闭合的内容块。

    Attributes:
        kind: ``content_block_start`` 报告的内容块类型。
        tool_use_id: 工具调用 ID；非 tool_use 内容块或上游未提供时为 None。
        tool_name: 工具名称；非 tool_use 内容块或上游未提供时为 None。
        json_chunks: 按到达顺序保存的工具参数 JSON 分片。
    """

    kind: str
    tool_use_id: str | None = None
    tool_name: str | None = None
    json_chunks: list[str] = field(default_factory=list)


class DeltaAccumulator:
    """按内容块 index 拼接一轮 assistant 消息中的工具参数。"""

    def __init__(self) -> None:
        """创建一个空的工具输入分片表。"""

        self._blocks: dict[int, _Block] = {}

    def on_block_start(self, index: int, content_block: dict[str, Any]) -> None:
        """登记新开始的内容块，供后续工具参数分片按 index 归并。

        Args:
            index: Anthropic stream_event 为内容块分配的消息内序号。
            content_block: ``content_block_start`` 携带的内容块对象。
        """

        kind = content_block.get("type", "")
        self._blocks[index] = _Block(
            kind=kind,
            tool_use_id=content_block.get("id"),
            tool_name=content_block.get("name"),
        )

    def on_input_json_delta(self, index: int, partial_json: str) -> None:
        """把工具参数 JSON 分片追加到对应的内容块。

        找不到 ``index`` 对应的开始事件时忽略该分片。

        Args:
            index: 该分片所属的消息内内容块序号。
            partial_json: ``input_json_delta.partial_json`` 携带的原始文本。
        """

        block = self._blocks.get(index)
        if block is not None:
            block.json_chunks.append(partial_json)

    def on_block_stop(self, index: int) -> ToolBlockResult | None:
        """移除已闭合的内容块，并组装完整的工具调用参数。

        Args:
            index: ``content_block_stop`` 指定的消息内内容块序号。

        Returns:
            已闭合的工具调用；找不到内容块、内容块不是 tool_use 或缺少工具调用
            ID 时为 None。没有参数分片或 JSON 无法解析时仍返回工具身份，但参数
            为空字典。
        """

        block = self._blocks.pop(index, None)
        if block is None or block.kind != "tool_use" or not block.tool_use_id:
            return None
        raw = "".join(block.json_chunks)
        try:
            parsed: dict[str, Any] = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            parsed = {}
        return ToolBlockResult(
            tool_use_id=block.tool_use_id,
            tool_name=block.tool_name or "",
            input=parsed,
        )

    def reset(self) -> None:
        """丢弃所有尚未闭合的工具输入分片。"""

        self._blocks.clear()
