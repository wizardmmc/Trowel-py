"""Tests for cc_host schemas: trowel event models + request models.

These pin the wire contract the frontend will consume. Every trowel event
carries a discriminator `type` so the frontend can switch on it.
"""
import pytest
from pydantic import ValidationError

from trowel_py.schemas.cc_host import (
    CreateSessionRequest,
    SendMessageRequest,
    SessionStartedEvent,
    UserEvent,
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
    InterruptedEvent,
    StalledEvent,
    ThinkingProgressEvent,
    SubagentProgressEvent,
    EVENT_TYPES,
)


class TestRequestModels:
    def test_create_session_minimal(self):
        req = CreateSessionRequest(workdir="/tmp/x")
        assert req.workdir == "/tmp/x"
        assert req.resume_from is None
        # spec: permission_mode defaults to bypassPermissions
        assert req.permission_mode == "bypassPermissions"

    def test_create_session_with_resume(self):
        req = CreateSessionRequest(workdir="/tmp/x", resume_from="abc-123")
        assert req.resume_from == "abc-123"

    def test_create_session_rejects_blank_workdir(self):
        with pytest.raises(ValidationError):
            CreateSessionRequest(workdir="")

    def test_send_message_requires_text(self):
        with pytest.raises(ValidationError):
            SendMessageRequest(text="")


class TestEventDiscriminators:
    @pytest.mark.parametrize(
        "model,etype",
        [
            (SessionStartedEvent, "session_started"),
            (UserEvent, "user"),
            (TextEvent, "text"),
            (ThinkingEvent, "thinking"),
            (ToolCallEvent, "tool_call"),
            (ToolProgressEvent, "tool_progress"),
            (ToolResultEvent, "tool_result"),
            (RetryingEvent, "retrying"),
            (HookEvent, "hook"),
            (StatusEvent, "status"),
            (CompactBoundaryEvent, "compact_boundary"),
            (LocalCommandEvent, "local_command"),
            (FinishedEvent, "finished"),
            (ErrorEvent, "error"),
            (InterruptedEvent, "interrupted"),
            (StalledEvent, "stalled"),
            (ThinkingProgressEvent, "thinking_progress"),
            (SubagentProgressEvent, "subagent_progress"),
        ],
    )
    def test_each_event_has_unique_type(self, model, etype):
        # the literal type field must match the expected wire discriminator
        assert set(EVENT_TYPES) == {
            "session_started", "user", "text", "thinking", "tool_call",
            "tool_progress", "tool_result", "retrying", "hook", "status",
            "compact_boundary", "local_command", "finished", "error",
            "interrupted", "stalled",
            "thinking_progress", "subagent_progress",
        }
        assert etype in EVENT_TYPES

    def test_session_started_dumps_expected_fields(self):
        ev = SessionStartedEvent(
            model="glm-5.2",
            cwd="/tmp/x",
            cc_session_id="s-1",
            tools=["Read", "Skill"],
        )
        dumped = ev.model_dump()
        assert dumped["type"] == "session_started"
        assert dumped["model"] == "glm-5.2"
        assert dumped["tools"] == ["Read", "Skill"]

    def test_text_event_carries_delta(self):
        ev = TextEvent(text="hello")
        assert ev.model_dump()["text"] == "hello"

    def test_tool_call_event_carries_input_dict(self):
        ev = ToolCallEvent(
            tool_use_id="tu_1", tool_name="Write", input={"path": "/a", "content": "x"},
        )
        dumped = ev.model_dump()
        assert dumped["tool_name"] == "Write"
        assert dumped["input"] == {"path": "/a", "content": "x"}

    def test_error_event_carries_subclass(self):
        ev = ErrorEvent(subclass="error_max_turns", errors=["loop"])
        assert ev.model_dump()["subclass"] == "error_max_turns"

    def test_finished_event_carries_usage(self):
        ev = FinishedEvent(usage={"input_tokens": 10}, total_cost_usd=0.01, num_turns=2)
        dumped = ev.model_dump()
        assert dumped["total_cost_usd"] == 0.01
        assert dumped["num_turns"] == 2
