"""拼接 CC stream_event 中分片到达的 tool input。

上游 Anthropic 协议用 ``content_block_start`` 声明 block identity，随后通过
``input_json_delta.partial_json`` 传递参数，``content_block_stop`` 时才能得到完整
JSON。text/thinking delta 由 translator 直接处理，不进入本状态机。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class ToolBlockResult:
    """已闭合 tool_use block 的完整输入。"""

    tool_use_id: str
    tool_name: str
    input: dict[str, Any]


@dataclass
class _Block:
    """单个 assistant message 内按 index 隔离的未闭合 block。"""

    kind: str
    tool_use_id: str | None = None
    tool_name: str | None = None
    json_chunks: list[str] = field(default_factory=list)


class DeltaAccumulator:
    """按 content block index 拼接 tool input；assistant turn 之间必须 reset。"""

    def __init__(self) -> None:
        self._blocks: dict[int, _Block] = {}

    def on_block_start(self, index: int, content_block: dict[str, Any]) -> None:
        kind = content_block.get("type", "")
        self._blocks[index] = _Block(
            kind=kind,
            tool_use_id=content_block.get("id"),
            tool_name=content_block.get("name"),
        )

    def on_input_json_delta(self, index: int, partial_json: str) -> None:
        block = self._blocks.get(index)
        if block is not None:
            block.json_chunks.append(partial_json)

    def on_block_stop(self, index: int) -> ToolBlockResult | None:
        """关闭 block；JSON fragment 无法解析时保留工具身份并返回空 input。"""
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
        self._blocks.clear()
