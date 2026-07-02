"""Accumulate streaming deltas from CC's stream_event content blocks.

CC emits Anthropic-protocol streaming events under `stream_event`. Tool_use
inputs arrive as `input_json_delta` fragments whose `partial_json` strings
concatenate to the full JSON argument object. Text/thinking deltas are
self-contained and are passed through directly by the translator (not here).

This module only stitches tool_use input fragments and tracks block identity
(id/name) declared at content_block_start, so the translator can emit one
tool_call event with the complete input when the block closes.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class ToolBlockResult:
    """A completed tool_use block with its stitched input."""

    tool_use_id: str
    tool_name: str
    input: dict[str, Any]


@dataclass
class _Block:
    """In-flight state for one streaming content block within a turn.

    Holds the block kind (text / thinking / tool_use), the tool_use id+name
    declared at content_block_start, and the running list of input_json_delta
    fragments that concatenate into the tool's full argument JSON.
    """

    kind: str
    tool_use_id: str | None = None
    tool_name: str | None = None
    json_chunks: list[str] = field(default_factory=list)


class DeltaAccumulator:
    """Tracks open content blocks and stitches tool_use input_json deltas.

    Indices are per-assistant-message (CC's content_block index). Call
    reset() between assistant turns so stale indices never leak.
    """

    def __init__(self) -> None:
        """Initialize with no open blocks."""
        self._blocks: dict[int, _Block] = {}

    def on_block_start(self, index: int, content_block: dict[str, Any]) -> None:
        """Register a content_block_start. Captures tool_use id/name."""
        kind = content_block.get("type", "")
        self._blocks[index] = _Block(
            kind=kind,
            tool_use_id=content_block.get("id"),
            tool_name=content_block.get("name"),
        )

    def on_input_json_delta(self, index: int, partial_json: str) -> None:
        """Accumulate one input_json_delta fragment for a tool_use block."""
        block = self._blocks.get(index)
        if block is not None:
            block.json_chunks.append(partial_json)

    def on_block_stop(self, index: int) -> ToolBlockResult | None:
        """Close a block. Returns the tool_use result, or None if not tool_use.

        Malformed stitched JSON (e.g. stream interrupted mid-fragment) degrades
        to an empty input dict rather than raising — a turn shouldn't die over
        one unparseable tool argument.
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
        """Drop all in-flight blocks (call between assistant turns)."""
        self._blocks.clear()
