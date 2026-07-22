"""Tests for cc_host schemas: trowel event models + request models.

These pin the wire contract the frontend will consume. Every trowel event
carries a discriminator `type` so the frontend can switch on it.
"""
import pytest
from pydantic import ValidationError

from trowel_py.schemas.cc_host import (
    ContextUsageEvent,
    CreateSessionRequest,
    RevertRequest,
    SendMessageRequest,
    SessionStartedEvent,
    TurnStartEvent,
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
    StalledWarningEvent,
    ThinkingProgressEvent,
    SubagentProgressEvent,
    ElicitationRequestEvent,
    ModelChangedEvent,
    WorkflowTreeEvent,
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

    def test_revert_request_requires_turn_id(self):
        with pytest.raises(ValidationError):
            RevertRequest(turn_id="")

    def test_revert_request_accepts_id(self):
        req = RevertRequest(turn_id="abc123")
        assert req.turn_id == "abc123"


class TestSessionStartedRosters:
    """slice-027 C1: SessionStartedEvent carries the bare-name rosters from
    cc system.init (description is fetched separately via /cc/slash-items)."""

    def test_carries_slash_rosters(self):
        ev = SessionStartedEvent(
            model="glm-5.2", cwd="/tmp", cc_session_id="s", tools=[],
            slash_commands=["monthly-etf"], skills=["monthly-etf"],
            agents=["claude"],
        )
        assert ev.slash_commands == ["monthly-etf"]
        assert ev.skills == ["monthly-etf"]
        assert ev.agents == ["claude"]

    def test_rosters_default_empty(self):
        ev = SessionStartedEvent(model="m", cwd="/", cc_session_id="s", tools=[])
        assert ev.slash_commands == []
        assert ev.skills == []
        assert ev.agents == []


class TestModelChangedSchema:
    """slice-027 C2: emitted right after /model (or /effort) RestartSession so
    the StatusBar syncs immediately — without waiting for the next message's
    system.init (CC is lazy-restarted by the next send's _ensure_process)."""

    def test_carries_model_and_effort(self):
        ev = ModelChangedEvent(model="opus", effort="high")
        d = ev.model_dump()
        assert d["type"] == "model_changed"
        assert d["model"] == "opus"
        assert d["effort"] == "high"

    def test_defaults_none_means_follow_settings(self):
        """None = trowel is deferring to cc settings.json (no --model / --effort)."""
        ev = ModelChangedEvent()
        assert ev.model is None
        assert ev.effort is None


class TestTurnStartEvent:
    def test_carries_turn_id_and_revertible(self):
        ev = TurnStartEvent(turn_id="t-1", revertible=True)
        dumped = ev.model_dump()
        assert dumped["type"] == "turn_start"
        assert dumped["turn_id"] == "t-1"
        assert dumped["revertible"] is True

    def test_defaults_revertible_false_when_set(self):
        ev = TurnStartEvent(turn_id="t-2", revertible=False)
        assert ev.model_dump()["revertible"] is False


class TestEventDiscriminators:
    @pytest.mark.parametrize(
        "model,etype",
        [
            (ContextUsageEvent, "context_usage"),
            (SessionStartedEvent, "session_started"),
            (TurnStartEvent, "turn_start"),
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
            (StalledWarningEvent, "stalled_warning"),
            (ThinkingProgressEvent, "thinking_progress"),
            (SubagentProgressEvent, "subagent_progress"),
            (ElicitationRequestEvent, "elicit_request"),
            (ModelChangedEvent, "model_changed"),
            (WorkflowTreeEvent, "workflow_tree"),
        ],
    )
    def test_each_event_has_unique_type(self, model, etype):
        # the literal type field must match the expected wire discriminator
        assert set(EVENT_TYPES) == {
            "session_started", "turn_start", "user", "text", "thinking",
            "tool_call", "tool_progress", "tool_result", "retrying", "hook",
            "status", "compact_boundary", "local_command", "finished", "error",
            "interrupted", "stalled_warning",
            "thinking_progress", "subagent_progress",
            "elicit_request", "model_changed",
            "workflow_tree",
            # slice-074: session_exited is now in EVENT_TYPES (previously a
            # documentation-only gap that slice-074's envelope validation made
            # into a hard reject, breaking CC /exit).
            "session_exited",
            "context_usage",
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


class TestElicitationRequestSchema:
    def test_elicit_request_carries_questions_and_ids(self):
        e = ElicitationRequestEvent(
            tool_use_id="call_abc",
            request_id="req-1",
            questions=[{"question": "A or B?", "header": "Pref",
                        "options": [{"label": "A"}], "multiSelect": False}],
        )
        dumped = e.model_dump()
        assert dumped["type"] == "elicit_request"
        assert dumped["tool_use_id"] == "call_abc"
        assert dumped["request_id"] == "req-1"
        assert dumped["questions"][0]["header"] == "Pref"


def test_event_types_covers_every_trowel_event_subclass():
    """slice-074 gpt5.6 Info 6: EVENT_TYPES must contain the `type` literal of
    EVERY TrowelEvent subclass. Catches the class of bug where session_exited
    was missing — a hand-maintained set drifting from the union."""
    import inspect

    from trowel_py.schemas import cc_host as mod

    event_classes = [
        obj
        for _, obj in inspect.getmembers(mod, inspect.isclass)
        if issubclass(obj, mod._Event) and obj is not mod._Event
    ]
    literals = set()
    for cls in event_classes:
        # each _Event subclass sets a `type: Literal["..."]` default
        type_field = cls.model_fields.get("type")
        assert type_field is not None, f"{cls.__name__} has no type field"
        default = type_field.default
        assert isinstance(default, str), f"{cls.__name__} type default is not a str"
        literals.add(default)
    missing = literals - mod.EVENT_TYPES
    assert not missing, (
        f"TrowelEvent subclass type literals missing from EVENT_TYPES: {missing}"
    )
