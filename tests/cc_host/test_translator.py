"""Tests for cc_host.translator.Translator.

The translator turns raw CC stream-json events into trowel events. CC event
shapes here are constructed from the ground-truth fields the spikes actually
parsed (spikes/cc-input-probe.py, cc-skill-trigger-probe.py), not guessed.
"""
import pytest

from trowel_py.cc_host.translator import Translator
from trowel_py.schemas.cc_host import (
    SessionStartedEvent,
    TextEvent,
    ThinkingEvent,
    ToolCallEvent,
    ToolProgressEvent,
    ToolResultEvent,
    RetryingEvent,
    HookEvent,
    StatusEvent,
    CompactBoundaryEvent,
    LocalCommandEvent,
    FinishedEvent,
    ErrorEvent,
)


def cc(**kw):
    """Build a CC event dict (top-level type/subtype + whatever fields)."""
    return dict(kw)


class TestSystemInit:
    def test_init_translates_to_session_started(self):
        ev = cc(type="system", subtype="init", model="glm-5.2", cwd="/tmp/x",
                session_id="s-1", tools=["Read", "Skill"])
        out = Translator().translate(ev)
        assert len(out) == 1
        s = out[0]
        assert isinstance(s, SessionStartedEvent)
        assert s.cc_session_id == "s-1"
        assert s.model == "glm-5.2"
        assert s.tools == ["Read", "Skill"]


class TestTextAndThinking:
    def test_text_delta_translates_to_text_event(self):
        ev = {"type": "stream_event", "event": {
            "type": "content_block_delta", "index": 0,
            "delta": {"type": "text_delta", "text": "hel"}}}
        out = Translator().translate(ev)
        assert [type(x) for x in out] == [TextEvent]
        assert out[0].text == "hel"

    def test_thinking_delta_translates_to_thinking_event(self):
        ev = {"type": "stream_event", "event": {
            "type": "content_block_delta", "index": 0,
            "delta": {"type": "thinking_delta", "text": "pondering"}}}
        out = Translator().translate(ev)
        assert isinstance(out[0], ThinkingEvent)
        assert out[0].text == "pondering"


class TestToolCallStitching:
    def _block_start(self):
        return {"type": "stream_event", "event": {
            "type": "content_block_start", "index": 0,
            "content_block": {"type": "tool_use", "id": "tu_1", "name": "Write"}}}

    def _delta(self, chunk):
        return {"type": "stream_event", "event": {
            "type": "content_block_delta", "index": 0,
            "delta": {"type": "input_json_delta", "partial_json": chunk}}}

    def _stop(self):
        return {"type": "stream_event", "event": {
            "type": "content_block_stop", "index": 0}}

    def test_tool_use_stitched_to_tool_call_on_block_stop(self):
        t = Translator()
        assert t.translate(self._block_start()) == []
        assert t.translate(self._delta('{"path":"/a",')) == []
        assert t.translate(self._delta('"content":"x"}')) == []
        out = t.translate(self._stop())
        assert len(out) == 1
        call = out[0]
        assert isinstance(call, ToolCallEvent)
        assert call.tool_use_id == "tu_1"
        assert call.tool_name == "Write"
        assert call.input == {"path": "/a", "content": "x"}

    def test_assistant_envelope_backfills_tool_call_when_no_stream(self):
        # if stream_event deltas were missing, the assistant envelope carries
        # the full content blocks and must still produce a tool_call.
        t = Translator()
        envelope = {"type": "assistant", "message": {"content": [
            {"type": "tool_use", "id": "tu_z", "name": "Read", "input": {"path": "/b"}},
        ]}}
        out = t.translate(envelope)
        assert len(out) == 1
        assert isinstance(out[0], ToolCallEvent)
        assert out[0].tool_use_id == "tu_z"
        assert out[0].input == {"path": "/b"}

    def test_assistant_envelope_does_not_duplicate_already_streamed_call(self):
        t = Translator()
        t.translate(self._block_start())
        t.translate(self._delta('{"path":"/a"}'))
        t.translate(self._stop())  # emits tool_call tu_1
        envelope = {"type": "assistant", "message": {"content": [
            {"type": "tool_use", "id": "tu_1", "name": "Write", "input": {"path": "/a"}},
        ]}}
        out = t.translate(envelope)
        assert out == []  # already emitted, no duplicate


class TestAssistantEnvelopeTextThinking:
    """CC (glm backend) emits ALL content via assistant envelopes, not stream
    deltas — so text/thinking blocks in the envelope must be surfaced too.
    Ground truth: spikes/e1-stream-capture.py captured a real E1 turn and saw
    zero stream_event deltas; everything (thinking, tool_use, text) arrived as
    assistant envelopes. Without emitting text/thinking from the envelope, the
    model's final answer is silently dropped (slice024 E1 regression)."""

    def test_assistant_envelope_text_is_emitted(self):
        t = Translator()
        envelope = {"type": "assistant", "message": {"role": "assistant", "content": [
            {"type": "text", "text": "今天是 2026年7月2日，星期四"}]}}
        out = t.translate(envelope)
        assert len(out) == 1
        assert isinstance(out[0], TextEvent)
        assert out[0].text == "今天是 2026年7月2日，星期四"

    def test_assistant_envelope_thinking_is_emitted(self):
        t = Translator()
        envelope = {"type": "assistant", "message": {"role": "assistant", "content": [
            {"type": "thinking", "thinking": "需要先查日期"}]}}
        out = t.translate(envelope)
        assert len(out) == 1
        assert isinstance(out[0], ThinkingEvent)
        assert out[0].text == "需要先查日期"

    def test_assistant_envelope_emits_thinking_text_tool_use_in_order(self):
        t = Translator()
        envelope = {"type": "assistant", "message": {"role": "assistant", "content": [
            {"type": "thinking", "thinking": "reason here"},
            {"type": "text", "text": "the answer"},
            {"type": "tool_use", "id": "tu_9", "name": "Bash",
             "input": {"command": "date"}},
        ]}}
        out = t.translate(envelope)
        assert [type(x).__name__ for x in out] == [
            "ThinkingEvent", "TextEvent", "ToolCallEvent"]
        assert out[2].tool_use_id == "tu_9"


class TestToolProgressAndResult:
    def test_tool_progress_top_level_type(self):
        ev = {"type": "tool_progress", "tool_use_id": "tu_1",
              "tool_name": "Write", "elapsed_time_seconds": 1.5}
        out = Translator().translate(ev)
        assert len(out) == 1
        assert isinstance(out[0], ToolProgressEvent)
        assert out[0].elapsed_time_seconds == 1.5

    def test_tool_result_from_user_message(self):
        ev = {"type": "user", "message": {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "tu_1", "content": "ok"}]}}
        out = Translator().translate(ev)
        assert len(out) == 1
        assert isinstance(out[0], ToolResultEvent)
        assert out[0].tool_use_id == "tu_1"
        assert out[0].content == "ok"

    def test_tool_result_with_list_content_is_flattened(self):
        ev = {"type": "user", "message": {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "tu_1",
             "content": [{"type": "text", "text": "a"}, {"type": "text", "text": "b"}]}]}}
        out = Translator().translate(ev)
        assert out[0].content == "a\nb"

    def test_user_message_without_tool_result_yields_nothing(self):
        # CC also echoes back the user's own message envelope; ignore it.
        ev = {"type": "user", "message": {"role": "user", "content": [
            {"type": "text", "text": "hi"}]}}
        assert Translator().translate(ev) == []


class TestRetrying:
    def test_api_retry_translates_with_attempt_incrementing(self):
        t = Translator()
        ev1 = cc(type="system", subtype="api_retry", error_status=529,
                 error="overloaded", retry_delay_ms=3000)
        out1 = t.translate(ev1)
        assert len(out1) == 1
        assert isinstance(out1[0], RetryingEvent)
        assert out1[0].attempt == 1
        assert out1[0].error_status == 529
        assert out1[0].retry_delay_ms == 3000
        # second retry bumps attempt
        out2 = t.translate(ev1)
        assert out2[0].attempt == 2


class TestHookStatusCompactLocal:
    def test_hook_response_translates(self):
        ev = cc(type="system", subtype="hook_response",
                hook_name="SessionStart", outcome="ok")
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
        # best-effort: spec notes the exact shape needs verifying at impl time;
        # we surface the stage string if present, else a generic label.
        ev = cc(type="system", subtype="status", subtype2="compacting")
        out = Translator().translate(ev)
        assert len(out) == 1
        assert isinstance(out[0], StatusEvent)
        assert out[0].stage == "compacting"


class TestResult:
    def test_success_translates_to_finished(self):
        ev = cc(type="result", subtype="success", is_error=False,
                total_cost_usd=0.02, usage={"input_tokens": 10}, num_turns=2)
        out = Translator().translate(ev)
        assert len(out) == 1
        f = out[0]
        assert isinstance(f, FinishedEvent)
        assert f.total_cost_usd == 0.02
        assert f.num_turns == 2
        assert f.usage == {"input_tokens": 10}

    @pytest.mark.parametrize("sub", [
        "error_during_execution", "error_max_turns",
        "error_max_budget_usd", "error_max_structured_output_retries",
    ])
    def test_error_subclasses_translate_to_error_event(self, sub):
        ev = cc(type="result", subtype=sub, is_error=True,
                errors=["boom"], api_error_status=529)
        out = Translator().translate(ev)
        assert len(out) == 1
        assert isinstance(out[0], ErrorEvent)
        assert out[0].subclass == sub
        assert out[0].errors == ["boom"]
        assert out[0].api_error_status == 529


class TestIgnoreList:
    @pytest.mark.parametrize("ev", [
        {"type": "system", "subtype": "post_turn_summary"},
        {"type": "system", "subtype": "task_started"},
        {"type": "system", "subtype": "task_progress"},
        {"type": "system", "subtype": "task_notification"},
        {"type": "system", "subtype": "session_state_changed"},
        {"type": "system", "subtype": "files_persisted"},
        {"type": "system", "subtype": "elicitation_complete"},
        {"type": "system", "subtype": "prompt_suggestion"},
        {"type": "system", "subtype": "mcp_message"},
        {"type": "system", "subtype": "streamlined_stuff"},
        {"type": "system", "subtype": "thinking_tokens"},  # TODO verify shape
        {"type": "unknown_thing"},
    ])
    def test_ignored_event_yields_nothing(self, ev):
        assert Translator().translate(ev) == []
