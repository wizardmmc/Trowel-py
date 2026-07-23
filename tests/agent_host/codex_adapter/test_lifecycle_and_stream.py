from trowel_py.codex_host.events import CodexEventType
from trowel_py.schemas.agent_host import AGENT_EVENT_SCHEMA, AgentEvent

from .support import make_codex_event


def test_session_started_maps_runtime_fields(adapter) -> None:
    event = adapter.wrap(
        make_codex_event(
            CodexEventType.SESSION_STARTED,
            seq=1,
            payload={
                "model": "gpt-5.6-sol",
                "model_provider": "openai",
                "cwd": "/repo",
                "sandbox": {"mode": "workspace-write"},
                "approval_policy": {"policy": "on-request"},
                "permission_profile": ":workspace-write",
                "effective_sandbox": "workspace-write",
                "effective_approval": "on-request",
                "network_access": False,
            },
        )
    )

    assert isinstance(event, AgentEvent)
    assert event.schema_version == AGENT_EVENT_SCHEMA
    assert event.runtime == "codex"
    assert event.type == "session_started"
    assert event.payload["model"] == "gpt-5.6-sol"
    assert event.payload["cwd"] == "/repo"
    assert event.payload["cc_session_id"] == "thr-1"
    assert event.payload["tools"] == []
    assert event.payload["permission_profile"] == ":workspace-write"
    assert event.payload["effective_sandbox"] == "workspace-write"
    assert event.payload["effective_approval"] == "on-request"
    assert event.payload["network_access"] is False


def test_turn_started_maps_to_non_revertible_turn(adapter) -> None:
    event = adapter.wrap(
        make_codex_event(CodexEventType.TURN_STARTED, seq=2, item_id=None)
    )

    assert event.type == "turn_start"
    assert event.turn_id == "turn-1"
    assert event.payload["revertible"] is False


def test_model_changed_maps_effective_settings(adapter) -> None:
    event = adapter.wrap(
        make_codex_event(
            CodexEventType.MODEL_CHANGED,
            seq=3,
            payload={"model": "gpt-5.6-sol", "effort": "high"},
        )
    )

    assert event.type == "model_changed"
    assert event.payload == {"model": "gpt-5.6-sol", "effort": "high"}


def test_user_text_maps_to_shared_user_event(adapter) -> None:
    event = adapter.wrap(
        make_codex_event(CodexEventType.USER, seq=4, payload={"text": "hi"})
    )

    assert event.type == "user"
    assert event.payload == {"text": "hi"}


def test_assistant_delta_maps_to_text(adapter) -> None:
    event = adapter.wrap(
        make_codex_event(
            CodexEventType.ASSISTANT_DELTA,
            seq=5,
            item_id="item-1",
            payload={"delta": "hello "},
        )
    )

    assert event.type == "text"
    assert event.payload == {"text": "hello "}
    assert event.item_id == "item-1"


def test_reasoning_delta_maps_to_thinking(adapter) -> None:
    event = adapter.wrap(
        make_codex_event(
            CodexEventType.REASONING_DELTA,
            seq=6,
            item_id="item-2",
            payload={"delta": "considering "},
        )
    )

    assert event.type == "thinking"
    assert event.payload["text"] == "considering "


def test_assistant_message_is_dropped(adapter) -> None:
    event = adapter.wrap(
        make_codex_event(
            CodexEventType.ASSISTANT_MESSAGE,
            seq=7,
            payload={"text": "full text", "phase": "done"},
        )
    )

    assert event is None
