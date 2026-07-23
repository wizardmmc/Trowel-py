from trowel_py.codex_host.events import CodexEventType

from .support import make_codex_event


def test_usage_updated_keeps_token_breakdown(adapter) -> None:
    event = adapter.wrap(
        make_codex_event(
            CodexEventType.USAGE_UPDATED,
            seq=1,
            payload={
                "total": {
                    "totalTokens": 15495,
                    "inputTokens": 15475,
                    "cachedInputTokens": 9984,
                    "outputTokens": 20,
                    "reasoningOutputTokens": 0,
                },
                "last": {
                    "totalTokens": 15495,
                    "inputTokens": 15475,
                },
                "model_context_window": 258400,
            },
        )
    )

    assert event.type == "usage_updated"
    assert event.payload["total"]["totalTokens"] == 15495
    assert event.payload["total"]["cachedInputTokens"] == 9984
    assert event.payload["model_context_window"] == 258400


def test_rate_limit_updated_keeps_account_snapshot(adapter) -> None:
    event = adapter.wrap(
        make_codex_event(
            CodexEventType.RATE_LIMIT_UPDATED,
            seq=2,
            payload={
                "limit_id": "codex",
                "primary": {"usedPercent": 20, "resetsAt": 1784949908},
                "credits": {
                    "hasCredits": False,
                    "unlimited": False,
                    "balance": "0",
                },
                "plan_type": "pro",
                "rate_limit_reached_type": None,
            },
        )
    )

    assert event.type == "rate_limit_updated"
    assert event.payload["primary"]["usedPercent"] == 20
    assert event.payload["plan_type"] == "pro"
    assert event.payload["rate_limit_reached_type"] is None


def test_host_status_keeps_manager_lifecycle(adapter) -> None:
    event = adapter.wrap(
        make_codex_event(
            CodexEventType.HOST_STATUS,
            seq=3,
            thread_id=None,
            payload={"status": "host_exited", "reason": "eof", "exit_code": 1},
        )
    )

    assert event.type == "host_status"
    assert event.payload["status"] == "host_exited"
    assert event.payload["exit_code"] == 1


def test_approval_request_keeps_verified_payload(adapter) -> None:
    event = adapter.wrap(
        make_codex_event(
            CodexEventType.APPROVAL_REQUEST,
            seq=4,
            item_id="exec-1",
            payload={
                "request_id": "7-0",
                "approval_kind": "command_approval",
                "command": "printf PENDING",
                "cwd": "/repo",
                "reason": "Allow it?",
                "available_decisions": ["accept", "cancel"],
                "status": "pending",
                "decision": None,
                "auto_resolved": False,
                "resolution_reason": None,
            },
        )
    )

    assert event.type == "approval_request"
    assert event.payload["request_id"] == "7-0"
    assert event.payload["available_decisions"] == ["accept", "cancel"]


def test_compaction_marks_completed_phase(adapter) -> None:
    event = adapter.wrap(
        make_codex_event(
            CodexEventType.COMPACTION,
            seq=5,
            item_id="compact-1",
            payload={"kind": "contextCompaction"},
        )
    )

    assert event.type == "compaction"
    assert event.payload == {"kind": "contextCompaction", "phase": "completed"}


def test_finished_maps_null_cost_fields(adapter) -> None:
    event = adapter.wrap(
        make_codex_event(
            CodexEventType.FINISHED,
            seq=6,
            payload={
                "turn_id": "turn-1",
                "status": "completed",
                "duration_ms": 4200,
            },
        )
    )

    assert event.type == "finished"
    assert event.payload["total_cost_usd"] is None
    assert event.payload["num_turns"] is None


def test_interrupted_maps_to_shared_terminal_event(adapter) -> None:
    event = adapter.wrap(make_codex_event(CodexEventType.INTERRUPTED, seq=7))

    assert event.type == "interrupted"


def test_status_maps_stage_and_active_flags(adapter) -> None:
    event = adapter.wrap(
        make_codex_event(
            CodexEventType.STATUS,
            seq=8,
            payload={"status": "active", "active_flags": ("waiting",)},
        )
    )

    assert event.type == "status"
    assert event.payload == {"stage": "active", "active_flags": ["waiting"]}


def test_native_error_maps_to_non_terminal_retry(adapter) -> None:
    event = adapter.wrap(
        make_codex_event(
            CodexEventType.ERROR,
            seq=9,
            payload={
                "kind": "native_error",
                "error_type": "rate_limit",
                "message": "slow down",
                "will_retry": True,
            },
        )
    )

    assert event.type == "retrying"
    assert event.payload["error"] == "slow down"


def test_failed_turn_maps_to_terminal_error(adapter) -> None:
    event = adapter.wrap(
        make_codex_event(
            CodexEventType.ERROR,
            seq=10,
            payload={
                "turn_id": "turn-1",
                "status": "failed",
                "error": "model refused",
                "duration_ms": 1000,
            },
        )
    )

    assert event.type == "error"
    assert event.payload["subclass"] == "turn_failed"
    assert event.payload["errors"] == ["model refused"]
