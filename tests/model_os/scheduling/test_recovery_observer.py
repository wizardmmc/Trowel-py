from datetime import datetime, timezone

from trowel_py.model_os.scheduling.recovery import (
    SwitchRecoveryObserver,
    record_switch_started,
)
from trowel_py.model_os.types import EventKind


def _events(store, kind: str):
    return [event for _, event in store.list_events() if event.kind == kind]


def test_first_tool_action_records_latency_and_only_a_redacted_fingerprint(
    store,
) -> None:
    record_switch_started(
        store,
        decision_id="decision.attention.sample",
        task_id="task-1",
        episode_id="episode-1",
        claimed_at="2026-07-26T00:00:00+00:00",
    )
    observer = SwitchRecoveryObserver(
        store,
        now=lambda: datetime(2026, 7, 26, 0, 0, 2, tzinfo=timezone.utc),
    )

    observer.observe_task_event(
        "task-1",
        {
            "type": "tool_call",
            "payload": {
                "tool_name": "Bash",
                "command": "cat /private/secret-file",
            },
        },
    )

    observed = _events(store, EventKind.SWITCH_RECOVERY_OBSERVED)[0]
    assert observed.payload["restore_latency_ms"] == 2000
    assert observed.payload["first_action_kind"] == "tool_call"
    assert observed.payload["tool_name"] == "Bash"
    assert observed.payload["tool_target_hash"].startswith("sha256:")
    assert "secret-file" not in str(observed.payload)


def test_terminal_without_tool_records_explicit_unavailable_reason(store) -> None:
    record_switch_started(
        store,
        decision_id="decision.attention.no-tool",
        task_id="task-2",
        episode_id="episode-2",
        claimed_at="2026-07-26T00:00:00+00:00",
    )
    observer = SwitchRecoveryObserver(store)

    observer.observe_task_event(
        "task-2",
        {"type": "finished", "payload": {}},
    )

    observed = _events(store, EventKind.SWITCH_RECOVERY_OBSERVED)[0]
    assert observed.payload["unavailable_reason"] == "no_tool_action"
    assert observed.payload["restore_latency_ms"] is None


def test_kernel_control_messages_do_not_count_as_first_action(store) -> None:
    record_switch_started(
        store,
        decision_id="decision.attention.control",
        task_id="task-3",
        episode_id="episode-3",
        claimed_at="2026-07-26T00:00:00+00:00",
    )
    observer = SwitchRecoveryObserver(store)

    observer.observe_task_event(
        "task-3",
        {"type": "assistant_delta", "payload": {"text": "control"}},
    )

    assert _events(store, EventKind.SWITCH_RECOVERY_OBSERVED) == []


def test_repeated_tool_candidate_ignores_per_call_identity(store) -> None:
    observer = SwitchRecoveryObserver(store)
    for index in (1, 2):
        record_switch_started(
            store,
            decision_id=f"decision.attention.repeat-{index}",
            task_id="task-repeat",
            episode_id=f"episode-{index}",
            claimed_at=f"2026-07-26T00:00:0{index}+00:00",
        )
        observer.observe_task_event(
            "task-repeat",
            {
                "type": "tool_call",
                "payload": {
                    "tool_use_id": f"call-{index}",
                    "tool_name": "Bash",
                    "input": {"command": "rg pattern", "cwd": "/repo"},
                    "started_at_ms": index,
                },
            },
        )

    observed = _events(store, EventKind.SWITCH_RECOVERY_OBSERVED)
    assert observed[1].payload["repeated_tool_candidates"]
    assert "call-" not in str(observed[1].payload)
