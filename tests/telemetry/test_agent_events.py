"""验证统一 Agent 事件只生成去正文的 turn 与 MCP span。"""

from __future__ import annotations

import json
from pathlib import Path

from trowel_py.telemetry.agent_events import AgentTelemetryObserver
from trowel_py.telemetry.events import activate_trace_context, create_span_context


class CapturingPort:
    """保存 Agent 事件观察器提交的 span。"""

    def __init__(self) -> None:
        self.spans = []

    def emit_span(self, span):
        self.spans.append(span)

    def emit_metric(self, _metric):
        return None


def test_recorded_dual_runtime_mcp_calls_do_not_propagate_traceparent() -> None:
    """真实脱敏录制固定 native 边界只能使用 span link 的事实。"""

    fixture = (
        Path(__file__).parent / "fixtures" / "mcp-trace-probe-2.1.197-0.144.0.json"
    )
    recorded = json.loads(fixture.read_text(encoding="utf-8"))

    assert recorded["stimulus"]["valid_parent_trace_in_runtime_environment"]
    for runtime in ("claude_code", "codex"):
        observation = recorded[runtime]
        assert observation["returncode"] == 0
        assert observation["tool_call_count"] == 1
        assert observation["tool_call_traceparent_present"] is False
        assert observation["tool_call_tracestate_present"] is False


def test_mcp_event_is_linked_to_turn_without_copying_payload_body() -> None:
    port = CapturingPort()
    observer = AgentTelemetryObserver(port)
    http = create_span_context()
    with activate_trace_context(http):
        observer.prepare_turn("session-secret", "codex")
    observer(
        {
            "schema": "agent-event-v1",
            "session_id": "session-secret",
            "runtime": "codex",
            "seq": 1,
            "type": "turn_start",
            "turn_id": "turn-secret",
            "item_id": None,
            "payload": {"revertible": False},
        }
    )
    observer(
        {
            "schema": "agent-event-v1",
            "session_id": "session-secret",
            "runtime": "codex",
            "seq": 2,
            "type": "tool_call",
            "turn_id": "turn-secret",
            "item_id": "tool-secret",
            "payload": {
                "tool_use_id": "tool-secret",
                "tool_name": "trowel_note_search.search",
                "input": {
                    "server": "trowel_note_search",
                    "tool": "search",
                    "arguments": {"query": "private prompt"},
                },
            },
        }
    )
    observer(
        {
            "schema": "agent-event-v1",
            "session_id": "session-secret",
            "runtime": "codex",
            "seq": 3,
            "type": "tool_result",
            "turn_id": "turn-secret",
            "item_id": "tool-secret",
            "payload": {
                "tool_use_id": "tool-secret",
                "tool_name": "trowel_note_search.search",
                "content": "private tool result",
                "status": "completed",
                "duration_ms": 12,
            },
        }
    )
    observer(
        {
            "schema": "agent-event-v1",
            "session_id": "session-secret",
            "runtime": "codex",
            "seq": 4,
            "type": "finished",
            "turn_id": "turn-secret",
            "item_id": None,
            "payload": {"usage": {"input": 100}},
        }
    )

    turn = next(span for span in port.spans if span.operation == "agent.turn")
    runtime_call = next(span for span in port.spans if span.operation == "runtime.call")
    mcp = next(span for span in port.spans if span.operation == "mcp.tools.call")
    assert turn.trace_id == http.trace_id
    assert turn.parent_span_id == http.span_id
    assert turn.links == []
    assert runtime_call.parent_span_id is None
    assert runtime_call.links[0].trace_id == turn.trace_id
    assert runtime_call.attributes.black_box is True
    assert mcp.links[0].trace_id == runtime_call.trace_id
    assert mcp.parent_span_id is None
    assert mcp.attributes.black_box is None
    rendered = "\n".join(span.model_dump_json() for span in port.spans)
    assert "private prompt" not in rendered
    assert "private tool result" not in rendered


def test_old_request_cleanup_cannot_abort_the_next_turn() -> None:
    """旧流延迟执行 finally 时不能误收口同一 session 的新 turn。"""

    port = CapturingPort()
    observer = AgentTelemetryObserver(port)
    old_observation = observer.prepare_turn("shared-session", "codex")
    observer(
        {
            "session_id": "shared-session",
            "runtime": "codex",
            "type": "turn_start",
            "turn_id": "old-turn",
        }
    )
    observer(
        {
            "session_id": "shared-session",
            "runtime": "codex",
            "type": "finished",
        }
    )

    new_observation = observer.prepare_turn("shared-session", "codex")
    observer(
        {
            "session_id": "shared-session",
            "runtime": "codex",
            "type": "turn_start",
            "turn_id": "new-turn",
        }
    )
    observer.abort_turn("shared-session", old_observation)
    observer(
        {
            "session_id": "shared-session",
            "runtime": "codex",
            "type": "finished",
        }
    )

    turns = [span for span in port.spans if span.operation == "agent.turn"]
    assert old_observation != new_observation
    assert [span.status for span in turns] == ["ok", "ok"]


def test_rejected_concurrent_prepare_cannot_erase_the_accepted_turn_parent() -> None:
    """后到但被拒绝的请求不能覆盖尚未收到 turn_start 的首个请求。"""

    port = CapturingPort()
    observer = AgentTelemetryObserver(port)
    first_http = create_span_context()
    second_http = create_span_context()
    with activate_trace_context(first_http):
        first_observation = observer.prepare_turn("shared-session", "codex")
    with activate_trace_context(second_http):
        second_observation = observer.prepare_turn("shared-session", "codex")

    observer.abort_turn("shared-session", second_observation)
    observer(
        {
            "session_id": "shared-session",
            "runtime": "codex",
            "type": "turn_start",
            "turn_id": "accepted-turn",
        }
    )
    observer(
        {
            "session_id": "shared-session",
            "runtime": "codex",
            "type": "finished",
        }
    )

    turn = next(span for span in port.spans if span.operation == "agent.turn")
    assert first_observation != second_observation
    assert turn.trace_id == first_http.trace_id
    assert turn.parent_span_id == first_http.span_id
