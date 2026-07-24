import pytest

from trowel_py.cc_host.translator import Translator
from trowel_py.schemas.cc_host import (
    CompactBoundaryEvent,
    ErrorEvent,
    FinishedEvent,
    HookEvent,
    LocalCommandEvent,
    RetryingEvent,
    StatusEvent,
    ThinkingProgressEvent,
    ToolProgressEvent,
    ToolResultEvent,
)
from tests.cc_host.translator._support import cc


class TestToolProgressAndResult:
    def test_tool_progress_top_level_type(self):
        ev = {
            "type": "tool_progress",
            "tool_use_id": "tu_1",
            "tool_name": "Write",
            "elapsed_time_seconds": 1.5,
        }
        out = Translator().translate(ev)
        assert len(out) == 1
        assert isinstance(out[0], ToolProgressEvent)
        assert out[0].elapsed_time_seconds == 1.5

    def test_tool_result_from_user_message(self):
        ev = {
            "type": "user",
            "message": {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "tu_1",
                        "content": "ok",
                    }
                ],
            },
        }
        out = Translator().translate(ev)
        assert len(out) == 1
        assert isinstance(out[0], ToolResultEvent)
        assert out[0].tool_use_id == "tu_1"
        assert out[0].content == "ok"

    def test_tool_result_with_list_content_is_flattened(self):
        ev = {
            "type": "user",
            "message": {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "tu_1",
                        "content": [
                            {"type": "text", "text": "a"},
                            {"type": "text", "text": "b"},
                        ],
                    }
                ],
            },
        }
        out = Translator().translate(ev)
        assert out[0].content == "a\nb"

    def test_tool_result_attaches_write_diff_from_tool_use_result(self):
        ev = {
            "type": "user",
            "message": {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "tu_e1",
                        "content": "The file x.py has been updated successfully.",
                    }
                ],
            },
            "tool_use_result": {
                "filePath": "/a/x.py",
                "structuredPatch": [
                    {
                        "oldStart": 42,
                        "oldLines": 1,
                        "newStart": 42,
                        "newLines": 1,
                        "lines": ["-x", "+y"],
                    }
                ],
            },
        }
        out = Translator().translate(ev)
        assert isinstance(out[0], ToolResultEvent)
        assert out[0].write_diff is not None
        assert out[0].write_diff.type == "update"
        assert out[0].write_diff.hunks[0].oldStart == 42
        assert out[0].write_diff.hunks[0].lines == ("-x", "+y")

    def test_tool_result_without_tool_use_result_has_no_write_diff(self):
        ev = {
            "type": "user",
            "message": {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "tu_b1",
                        "content": "ok",
                    }
                ],
            },
        }
        out = Translator().translate(ev)
        assert isinstance(out[0], ToolResultEvent)
        assert out[0].write_diff is None

    def test_user_message_without_tool_result_yields_nothing(self):
        ev = {
            "type": "user",
            "message": {
                "role": "user",
                "content": [{"type": "text", "text": "hi"}],
            },
        }
        assert Translator().translate(ev) == []


class TestRetrying:
    def test_api_retry_passes_through_cc_attempt(self):
        t = Translator()
        ev1 = cc(
            type="system",
            subtype="api_retry",
            attempt=1,
            error_status=529,
            error="overloaded",
            retry_delay_ms=3000,
        )
        out1 = t.translate(ev1)
        assert len(out1) == 1
        assert isinstance(out1[0], RetryingEvent)
        assert out1[0].attempt == 1
        assert out1[0].error_status == 529
        assert out1[0].retry_delay_ms == 3000
        out2 = t.translate(cc(type="system", subtype="api_retry", attempt=1))
        assert out2[0].attempt == 1

    def test_api_retry_without_attempt_falls_back_to_zero(self):
        ev = cc(type="system", subtype="api_retry", error_status=529)
        out = Translator().translate(ev)
        assert len(out) == 1
        assert out[0].attempt == 0

    def test_api_retry_coerces_fractional_float_fields_to_int(self):
        ev = cc(
            type="system",
            subtype="api_retry",
            error_status=529.0,
            error="overloaded",
            retry_delay_ms=574.0467280609035,
            max_retries=10.0,
        )
        out = Translator().translate(ev)
        assert len(out) == 1
        assert isinstance(out[0], RetryingEvent)
        assert out[0].retry_delay_ms == 574
        assert out[0].error_status == 529
        assert out[0].max_retries == 10
        assert isinstance(out[0].retry_delay_ms, int)


class TestHookStatusCompactLocal:
    def test_hook_response_translates(self):
        ev = cc(
            type="system",
            subtype="hook_response",
            hook_name="SessionStart",
            outcome="ok",
        )
        out = Translator().translate(ev)
        assert len(out) == 1
        assert isinstance(out[0], HookEvent)
        assert out[0].hook_name == "SessionStart"
        assert out[0].outcome == "ok"

    def test_compact_boundary_translates(self):
        ev = cc(type="system", subtype="compact_boundary")
        out = Translator().translate(ev)
        assert len(out) == 1
        assert isinstance(out[0], CompactBoundaryEvent)

    def test_local_command_output_translates(self):
        ev = cc(type="system", subtype="local_command_output", content="cost: 0.5")
        out = Translator().translate(ev)
        assert len(out) == 1
        assert isinstance(out[0], LocalCommandEvent)
        assert out[0].content == "cost: 0.5"

    def test_status_compacting_translates(self):
        ev = cc(type="system", subtype="status", subtype2="compacting")
        out = Translator().translate(ev)
        assert len(out) == 1
        assert isinstance(out[0], StatusEvent)
        assert out[0].stage == "compacting"


class TestResult:
    def test_success_translates_to_finished(self):
        ev = cc(
            type="result",
            subtype="success",
            is_error=False,
            total_cost_usd=0.02,
            usage={"input_tokens": 10},
            num_turns=2,
        )
        out = Translator().translate(ev)
        assert len(out) == 1
        f = out[0]
        assert isinstance(f, FinishedEvent)
        assert f.total_cost_usd == 0.02
        assert f.num_turns == 2
        assert f.usage == {"input_tokens": 10}

    @pytest.mark.parametrize(
        "sub",
        [
            "error_during_execution",
            "error_max_turns",
            "error_max_budget_usd",
            "error_max_structured_output_retries",
        ],
    )
    def test_error_subclasses_translate_to_error_event(self, sub):
        ev = cc(
            type="result",
            subtype=sub,
            is_error=True,
            errors=["boom"],
            api_error_status=529,
        )
        out = Translator().translate(ev)
        assert len(out) == 1
        assert isinstance(out[0], ErrorEvent)
        assert out[0].subclass == sub
        assert out[0].errors == ["boom"]
        assert out[0].api_error_status == 529


class TestThinkingProgress:
    def test_thinking_tokens_translates_to_thinking_progress(self):
        ev = cc(
            type="system",
            subtype="thinking_tokens",
            estimated_tokens=26,
            estimated_tokens_delta=2,
        )
        out = Translator().translate(ev)
        assert len(out) == 1
        assert isinstance(out[0], ThinkingProgressEvent)
        assert out[0].estimated_tokens == 26

    def test_each_heartbeat_maps_independently(self):
        t = Translator()
        t.translate(
            cc(
                type="system",
                subtype="thinking_tokens",
                estimated_tokens=1,
                estimated_tokens_delta=1,
            )
        )
        out = t.translate(
            cc(
                type="system",
                subtype="thinking_tokens",
                estimated_tokens=3,
                estimated_tokens_delta=2,
            )
        )
        assert isinstance(out[0], ThinkingProgressEvent)
        assert out[0].estimated_tokens == 3
