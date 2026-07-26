from __future__ import annotations

import pytest

from trowel_py.codex_host.errors import ProtocolViolationError
from trowel_py.codex_host.events import CodexEventType
from trowel_py.codex_host.translator import CodexTranslator

from ._support import _subagent_notifications


def test_recorded_v2_activity_keeps_child_thread_and_path() -> None:
    message = _subagent_notifications()[0]

    event = CodexTranslator().translate(message["method"], message["params"])[0]

    assert event.type is CodexEventType.SUBAGENT_ACTIVITY
    assert event.thread_id == "parent-thread-1"
    assert event.turn_id == "parent-turn-1"
    assert event.item_id == "spawn-activity-1"
    assert dict(event.payload) == {
        "source": "subagent_activity",
        "kind": "started",
        "agent_thread_id": "child-thread-1",
        "agent_path": "/root/probe",
    }


def test_recorded_collab_wait_keeps_native_sparse_fields() -> None:
    started, completed = _subagent_notifications()[-2:]
    translator = CodexTranslator()

    events = [
        translator.translate(message["method"], message["params"])[0]
        for message in (started, completed)
    ]

    assert [event.type for event in events] == [
        CodexEventType.SUBAGENT_ACTIVITY,
        CodexEventType.SUBAGENT_ACTIVITY,
    ]
    assert [event.payload["status"] for event in events] == [
        "inProgress",
        "completed",
    ]
    assert events[0].payload["source"] == "collab_agent_tool_call"
    assert events[0].payload["tool"] == "wait"
    assert events[0].payload["receiver_thread_ids"] == ()
    assert events[0].payload["prompt"] is None
    assert events[0].payload["model"] is None
    assert events[0].payload["reasoning_effort"] is None


def test_v2_activity_rejects_missing_agent_thread_id() -> None:
    message = _subagent_notifications()[0]
    del message["params"]["item"]["agentThreadId"]

    with pytest.raises(ProtocolViolationError, match="agentThreadId"):
        CodexTranslator().translate(message["method"], message["params"])
