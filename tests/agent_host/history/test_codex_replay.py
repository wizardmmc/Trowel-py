from __future__ import annotations

import json
from pathlib import Path

from trowel_py.agent_host.codex_adapter import CodexEventAdapter
from trowel_py.codex_host.history import events_from_thread


def test_recorded_thread_read_replays_user_assistant_and_mcp() -> None:
    fixture = (
        Path(__file__).parents[2]
        / "codex_host"
        / "fixtures"
        / "thread-read-0.144.0.json"
    )
    recorded = json.loads(fixture.read_text(encoding="utf-8"))
    native_events = events_from_thread("history-codex", recorded["thread"])
    adapter = CodexEventAdapter("history-codex")
    envelopes = [mapped for event in native_events if (mapped := adapter.wrap(event))]

    assert [event.type for event in envelopes] == [
        "user",
        "text",
        "tool_call",
        "tool_result",
        "finished",
    ]
    assert [event.seq for event in envelopes] == [1, 2, 3, 4, 5]
    assert envelopes[1].payload["text"] == "sanitized assistant message"
    assert envelopes[2].payload["tool_name"] == "trowel_note_search.search"
    assert envelopes[3].payload["status"] == "completed"
    assert isinstance(envelopes[3].payload["content"], str)
