from __future__ import annotations

from trowel_py.codex_host.events import CodexEventType
from trowel_py.codex_host.history import events_from_thread


def test_parent_history_rebuilds_recorded_subagent_activity() -> None:
    events = events_from_thread(
        "session-1",
        {
            "id": "parent-thread-1",
            "turns": [
                {
                    "id": "parent-turn-1",
                    "status": "completed",
                    "items": [
                        {
                            "id": "activity-1",
                            "type": "subAgentActivity",
                            "kind": "started",
                            "agentThreadId": "child-thread-1",
                            "agentPath": "/root/probe",
                        }
                    ],
                }
            ],
        },
    )

    activity = next(
        event for event in events if event.type is CodexEventType.SUBAGENT_ACTIVITY
    )
    assert activity.thread_id == "parent-thread-1"
    assert activity.payload["agent_thread_id"] == "child-thread-1"
    assert activity.payload["agent_path"] == "/root/probe"
