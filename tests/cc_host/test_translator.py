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
    ThinkingProgressEvent,
    SubagentProgressEvent,
    ElicitationRequestEvent,
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

    def test_init_passes_through_slash_rosters(self):
        """slice-027 C1: cc init's slash_commands/skills/agents are bare name
        lists (coreSchemas.ts z.array(z.string())). Translator passes them
        through so the frontend '/' autocomplete knows the roster. Description
        is NOT here — it comes from GET /cc/slash-items (frontmatter loader)."""
        ev = cc(type="system", subtype="init", model="glm-5.2", cwd="/tmp/x",
                session_id="s-1", tools=["Read"],
                slash_commands=["monthly-etf", "review"],
                skills=["monthly-etf"],
                agents=["claude", "general-purpose"])
        out = Translator().translate(ev)
        s = out[0]
        assert s.slash_commands == ["monthly-etf", "review"]
        assert s.skills == ["monthly-etf"]
        assert s.agents == ["claude", "general-purpose"]

    def test_init_without_rosters_defaults_empty(self):
        """Minimal init fixtures (or older CC versions) without these fields
        default to empty lists, not None — keeps the frontend's reducer simple."""
        ev = cc(type="system", subtype="init", model="glm-5.2", cwd="/tmp/x",
                session_id="s-1", tools=["Read"])
        s = Translator().translate(ev)[0]
        assert s.slash_commands == []
        assert s.skills == []
        assert s.agents == []


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

    def test_subagent_tool_use_carries_parent_tool_use_id(self):
        # a sub-agent's internal tool_use envelope carries parent_tool_use_id
        # pointing at the spawning Agent tool_call (slice-025-a problem 2 data).
        envelope = {"type": "assistant", "parent_tool_use_id": "call_AGENT_X",
                    "subagent_type": "general-purpose",
                    "message": {"content": [
                        {"type": "tool_use", "id": "call_BASH_Y", "name": "Bash",
                         "input": {"command": "ls"}}]}}
        out = Translator().translate(envelope)
        assert len(out) == 1
        assert isinstance(out[0], ToolCallEvent)
        assert out[0].parent_tool_use_id == "call_AGENT_X"

    def test_top_level_tool_use_has_no_parent(self):
        # a top-level (main cc) tool_use envelope has no parent_tool_use_id
        envelope = {"type": "assistant", "message": {"content": [
            {"type": "tool_use", "id": "tu_main", "name": "Bash",
             "input": {"command": "pwd"}}]}}
        out = Translator().translate(envelope)
        assert out[0].parent_tool_use_id is None


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

    def test_tool_result_attaches_write_diff_from_tool_use_result(self):
        """slice-033 feat 2 (方案 F): the stream-json `user` envelope carries
        cc's pre-computed diff in its top-level `tool_use_result` field; it's
        attached to the ToolResultEvent so live renders real file line numbers
        (same as replay)."""
        ev = {
            "type": "user",
            "message": {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "tu_e1",
                 "content": "The file x.py has been updated successfully."}]},
            "tool_use_result": {
                "filePath": "/a/x.py",
                "structuredPatch": [
                    {"oldStart": 42, "oldLines": 1, "newStart": 42, "newLines": 1,
                     "lines": ["-x", "+y"]}],
            },
        }
        out = Translator().translate(ev)
        assert isinstance(out[0], ToolResultEvent)
        assert out[0].write_diff is not None
        assert out[0].write_diff.type == "update"
        assert out[0].write_diff.hunks[0].oldStart == 42
        assert out[0].write_diff.hunks[0].lines == ("-x", "+y")

    def test_tool_result_without_tool_use_result_has_no_write_diff(self):
        """Live tool_result with no tool_use_result (Bash, …) → no write_diff."""
        ev = {"type": "user", "message": {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "tu_b1", "content": "ok"}]}}
        out = Translator().translate(ev)
        assert isinstance(out[0], ToolResultEvent)
        assert out[0].write_diff is None

    def test_user_message_without_tool_result_yields_nothing(self):
        # CC also echoes back the user's own message envelope; ignore it.
        ev = {"type": "user", "message": {"role": "user", "content": [
            {"type": "text", "text": "hi"}]}}
        assert Translator().translate(ev) == []


class TestRetrying:
    def test_api_retry_passes_through_cc_attempt(self):
        """slice-035 bug3: CC's api_retry event carries its own `attempt`
        (QueryEngine.ts `attempt: message.retryAttempt`; withRetry.ts
        `for attempt=1..` — each API request starts a fresh count). Trowel
        must pass it through, NOT accumulate its own counter: the old counter
        never reset within a turn, so a second failure after a recovered
        retry round showed 'attempt 4' instead of 'attempt 1'.
        """
        t = Translator()
        ev1 = cc(type="system", subtype="api_retry", attempt=1,
                 error_status=529, error="overloaded", retry_delay_ms=3000)
        out1 = t.translate(ev1)
        assert len(out1) == 1
        assert isinstance(out1[0], RetryingEvent)
        assert out1[0].attempt == 1
        assert out1[0].error_status == 529
        assert out1[0].retry_delay_ms == 3000
        # same turn, a second failure arrives — CC's new retry round starts
        # at attempt=1 again. Pass-through must NOT accumulate to 2.
        out2 = t.translate(cc(type="system", subtype="api_retry", attempt=1))
        assert out2[0].attempt == 1

    def test_api_retry_without_attempt_falls_back_to_zero(self):
        """Defensive: if a backend omits `attempt`, fall back to 0 (the FE
        shows '重试中' with no number) rather than inventing a counter."""
        ev = cc(type="system", subtype="api_retry", error_status=529)
        out = Translator().translate(ev)
        assert len(out) == 1
        assert out[0].attempt == 0

    def test_api_retry_coerces_fractional_float_fields_to_int(self):
        """GLM backend can emit fractional floats for int fields (issue: int_from_float).

        retry_delay_ms=574.04... must be truncated to int at the translator
        boundary, otherwise Pydantic rejects the RetryingEvent. Same defense
        applies to max_retries / error_status.
        """
        ev = cc(type="system", subtype="api_retry",
                error_status=529.0, error="overloaded",
                retry_delay_ms=574.0467280609035, max_retries=10.0)
        out = Translator().translate(ev)
        assert len(out) == 1
        assert isinstance(out[0], RetryingEvent)
        assert out[0].retry_delay_ms == 574
        assert out[0].error_status == 529
        assert out[0].max_retries == 10
        assert isinstance(out[0].retry_delay_ms, int)


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


class TestThinkingProgress:
    """thinking_tokens heartbeats -> ThinkingProgressEvent (slice-025-a A1).

    On the GLM backend these are the ONLY signal during thinking (thinking content
    arrives in a later assistant envelope). The translator used to drop them with a
    'Revisit if a use appears' comment — A1 is that use.
    """

    def test_thinking_tokens_translates_to_thinking_progress(self):
        ev = cc(type="system", subtype="thinking_tokens",
                estimated_tokens=26, estimated_tokens_delta=2)
        out = Translator().translate(ev)
        assert len(out) == 1
        assert isinstance(out[0], ThinkingProgressEvent)
        assert out[0].estimated_tokens == 26

    def test_each_heartbeat_maps_independently(self):
        # no per-task state needed; each heartbeat maps 1:1 with its cumulative count
        t = Translator()
        t.translate(cc(type="system", subtype="thinking_tokens",
                  estimated_tokens=1, estimated_tokens_delta=1))
        out = t.translate(cc(type="system", subtype="thinking_tokens",
                       estimated_tokens=3, estimated_tokens_delta=2))
        assert isinstance(out[0], ThinkingProgressEvent)
        assert out[0].estimated_tokens == 3


class TestSubagentProgress:
    """task_started/progress/notification -> SubagentProgressEvent (slice-025-a A3).

    Ground truth: reverse_cc samples/raw/030_task_agenttool.jsonl. task_updated is
    dropped (no tool_use_id, duplicates notification — decision #5).
    """

    def test_task_started_translates(self):
        ev = cc(type="system", subtype="task_started", task_id="t1",
                tool_use_id="call_x", description="Count files",
                subagent_type="general-purpose", task_type="local_agent",
                prompt="count")
        out = Translator().translate(ev)
        assert len(out) == 1
        e = out[0]
        assert isinstance(e, SubagentProgressEvent)
        assert e.tool_use_id == "call_x"
        assert e.task_id == "t1"
        assert e.status == "started"
        assert e.description == "Count files"
        assert e.subagent_type == "general-purpose"
        assert e.last_tool_name is None
        assert e.usage is None

    def test_task_progress_translates(self):
        ev = cc(type="system", subtype="task_progress", task_id="t1",
                tool_use_id="call_x", description="Running Count",
                subagent_type="general-purpose", last_tool_name="Bash",
                usage={"total_tokens": 10, "tool_uses": 1, "duration_ms": 4865})
        out = Translator().translate(ev)
        assert len(out) == 1
        e = out[0]
        assert isinstance(e, SubagentProgressEvent)
        assert e.status == "progress"
        assert e.last_tool_name == "Bash"
        assert e.usage == {"total_tokens": 10, "tool_uses": 1, "duration_ms": 4865}

    def test_task_notification_translates_to_completed(self):
        ev = cc(type="system", subtype="task_notification", task_id="t1",
                tool_use_id="call_x", status="completed",
                output_file="", summary="Count files",
                usage={"total_tokens": 0, "tool_uses": 2, "duration_ms": 13878})
        out = Translator().translate(ev)
        assert len(out) == 1
        e = out[0]
        assert isinstance(e, SubagentProgressEvent)
        assert e.status == "completed"
        assert e.usage == {"total_tokens": 0, "tool_uses": 2, "duration_ms": 13878}

    def test_task_updated_yields_nothing(self):
        # decision #5: no tool_use_id + duplicates task_notification; stay ignored
        ev = cc(type="system", subtype="task_updated", task_id="t1",
                patch={"status": "completed", "end_time": 1783036070663})
        assert Translator().translate(ev) == []

    def test_sample030_subagent_tail(self):
        # replay real sample 030 tail: assistant thinking + Agent tool_use, then
        # task_started/progress/updated/notification. Assert order + task_updated drop.
        t = Translator()
        t.translate({"type": "assistant", "message": {"content": [
            {"type": "thinking", "thinking": "need to count"}]}})
        t.translate({"type": "assistant", "message": {"content": [
            {"type": "tool_use", "id": "call_1ac8", "name": "Agent",
             "input": {"description": "Count files", "prompt": "...",
                       "subagent_type": "general-purpose"}}]}})
        started = t.translate(cc(type="system", subtype="task_started",
            task_id="a53f", tool_use_id="call_1ac8",
            description="Count files in directory",
            subagent_type="general-purpose", task_type="local_agent", prompt="..."))
        progress = t.translate(cc(type="system", subtype="task_progress",
            task_id="a53f", tool_use_id="call_1ac8", description="Running",
            subagent_type="general-purpose", last_tool_name="Bash",
            usage={"total_tokens": 0, "tool_uses": 1, "duration_ms": 4865}))
        updated = t.translate(cc(type="system", subtype="task_updated",
            task_id="a53f", patch={"status": "completed", "end_time": 1783036070663}))
        notification = t.translate(cc(type="system", subtype="task_notification",
            task_id="a53f", tool_use_id="call_1ac8", status="completed",
            output_file="", summary="Count files in directory",
            usage={"total_tokens": 0, "tool_uses": 2, "duration_ms": 13878}))
        assert isinstance(started[0], SubagentProgressEvent)
        assert started[0].status == "started"
        assert progress[0].status == "progress"
        assert progress[0].last_tool_name == "Bash"
        assert updated == []
        assert notification[0].status == "completed"


class TestIgnoreList:
    @pytest.mark.parametrize("ev", [
        {"type": "system", "subtype": "post_turn_summary"},
        {"type": "system", "subtype": "task_updated"},
        {"type": "system", "subtype": "session_state_changed"},
        {"type": "system", "subtype": "files_persisted"},
        {"type": "system", "subtype": "elicitation_complete"},
        {"type": "system", "subtype": "prompt_suggestion"},
        {"type": "system", "subtype": "mcp_message"},
        {"type": "system", "subtype": "streamlined_stuff"},
        {"type": "unknown_thing"},
    ])
    def test_ignored_event_yields_nothing(self, ev):
        assert Translator().translate(ev) == []


class TestElicitationRequest:
    """control_request(can_use_tool, AskUserQuestion) -> ElicitationRequestEvent (slice-025-c).

    Ground truth: reverse_cc samples/raw/052_askuser_bypass_stdio.jsonl. Route:
    bypassPermissions + --permission-prompt-tool stdio — ordinary tools stay
    silent (bypass auto-allow via permissions.ts 2a), only requiresUserInteraction
    tools (AskUserQuestion/EnterPlanMode/ExitPlanMode) emit control_request.
    """

    def test_askuser_control_request_translates(self):
        ev = cc(
            type="control_request",
            request_id="req-1",
            request={
                "subtype": "can_use_tool",
                "tool_name": "AskUserQuestion",
                "display_name": "AskUserQuestion",
                "input": {"questions": [
                    {"question": "A or B?", "header": "Pref",
                     "options": [{"label": "A", "description": "a"},
                                 {"label": "B"}],
                     "multiSelect": False}]},
                "tool_use_id": "call_abc",
            },
        )
        out = Translator().translate(ev)
        assert len(out) == 1
        e = out[0]
        assert isinstance(e, ElicitationRequestEvent)
        assert e.tool_use_id == "call_abc"
        assert e.request_id == "req-1"
        assert e.questions[0]["question"] == "A or B?"
        assert e.questions[0]["header"] == "Pref"
        assert e.questions[0]["multiSelect"] is False

    def test_non_askuser_control_request_yields_nothing(self):
        # Ordinary tools under bypass+stdio do not emit control_request (1e in
        # 2a). If one ever slips through, we do NOT render a selection box —
        # the permission-confirmation path is a separate slice (slice-025-c
        # only owns AskUserQuestion).
        ev = cc(
            type="control_request",
            request_id="req-2",
            request={"subtype": "can_use_tool", "tool_name": "Bash",
                     "input": {"command": "ls"}, "tool_use_id": "call_xyz"},
        )
        assert Translator().translate(ev) == []

    def test_non_can_use_tool_subtype_yields_nothing(self):
        ev = cc(
            type="control_request",
            request_id="req-3",
            request={"subtype": "set_max_thinking_tokens",
                     "max_thinking_tokens": 1024},
        )
        assert Translator().translate(ev) == []

    def test_askuserquestion_tool_use_skipped_no_tool_call(self):
        """AskUserQuestion tool_use does not produce ToolCallEvent — it is
        rendered inline as a selection box via the control_request path
        (ElicitationRequestEvent). Mirrors cc renderToolUseMessage=null."""
        ev = cc(
            type="assistant",
            message={"content": [
                {"type": "tool_use", "id": "call_aq", "name": "AskUserQuestion",
                 "input": {"questions": [{"question": "A?", "header": "P",
                                          "options": [{"label": "A"}],
                                          "multiSelect": False}]}},
            ]},
        )
        out = Translator().translate(ev)
        assert out == []
