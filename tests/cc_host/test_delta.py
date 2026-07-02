"""Tests for cc_host.delta.DeltaAccumulator.

CC streams tool_use inputs as partial JSON fragments across many
content_block_delta events (Anthropic streaming protocol). The accumulator
stitches them into a complete dict when the block closes, so the translator
can emit a single tool_call event with the full input.
"""
import pytest

from trowel_py.cc_host.delta import DeltaAccumulator, ToolBlockResult


class TestInputJsonAccumulation:
    def test_single_block_partial_json_stitches_into_dict(self):
        acc = DeltaAccumulator()
        acc.on_block_start(0, {"type": "tool_use", "id": "tu_1", "name": "Write"})
        acc.on_input_json_delta(0, '{"path": "/a",')
        acc.on_input_json_delta(0, ' "content": "x"}')
        result = acc.on_block_stop(0)
        assert isinstance(result, ToolBlockResult)
        assert result.tool_use_id == "tu_1"
        assert result.tool_name == "Write"
        assert result.input == {"path": "/a", "content": "x"}

    def test_empty_input_becomes_empty_dict(self):
        # a tool_use with no arguments streams no input_json_delta
        acc = DeltaAccumulator()
        acc.on_block_start(0, {"type": "tool_use", "id": "tu_2", "name": "List"})
        result = acc.on_block_stop(0)
        assert result is not None
        assert result.input == {}

    def test_non_tool_use_block_stop_returns_none(self):
        acc = DeltaAccumulator()
        acc.on_block_start(0, {"type": "text", "text": ""})
        assert acc.on_block_stop(0) is None

    def test_multiple_concurrent_blocks_tracked_by_index(self):
        acc = DeltaAccumulator()
        acc.on_block_start(0, {"type": "tool_use", "id": "tu_a", "name": "Read"})
        acc.on_block_start(1, {"type": "tool_use", "id": "tu_b", "name": "Write"})
        acc.on_input_json_delta(0, '{"p":"a"}')
        acc.on_input_json_delta(1, '{"p":"b"}')
        a = acc.on_block_stop(0)
        b = acc.on_block_stop(1)
        assert a.input == {"p": "a"}
        assert b.input == {"p": "b"}

    def test_unicode_in_partial_json(self):
        acc = DeltaAccumulator()
        acc.on_block_start(0, {"type": "tool_use", "id": "tu_u", "name": "X"})
        acc.on_input_json_delta(0, '{"msg":"你好"}')
        result = acc.on_block_stop(0)
        assert result.input == {"msg": "你好"}

    def test_reset_clears_blocks_between_turns(self):
        acc = DeltaAccumulator()
        acc.on_block_start(0, {"type": "tool_use", "id": "tu_1", "name": "Write"})
        acc.on_input_json_delta(0, '{"a":1}')
        acc.reset()
        # after reset, a stray stop on stale index yields nothing
        assert acc.on_block_stop(0) is None
