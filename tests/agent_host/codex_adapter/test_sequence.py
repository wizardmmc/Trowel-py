from trowel_py.codex_host.events import CodexEventType

from .support import make_codex_event


def test_native_turn_and_item_ids_reach_envelope(adapter) -> None:
    event = adapter.wrap(
        make_codex_event(
            CodexEventType.ASSISTANT_DELTA,
            seq=42,
            turn_id="turn-9",
            item_id="item-7",
            payload={"delta": "x"},
        )
    )

    assert event.turn_id == "turn-9"
    assert event.item_id == "item-7"
    assert event.session_id == "codex-sess"


def test_dropped_assistant_message_does_not_create_seq_gap(adapter) -> None:
    first = adapter.wrap(
        make_codex_event(
            CodexEventType.ASSISTANT_DELTA,
            seq=10,
            payload={"delta": "a"},
        )
    )
    dropped = adapter.wrap(
        make_codex_event(
            CodexEventType.ASSISTANT_MESSAGE,
            seq=11,
            payload={"text": "a"},
        )
    )
    third = adapter.wrap(
        make_codex_event(
            CodexEventType.ASSISTANT_DELTA,
            seq=12,
            payload={"delta": "b"},
        )
    )

    assert dropped is None
    assert first.seq == 1
    assert third.seq == 2


def test_tool_progress_is_dropped_without_seq_gap(adapter) -> None:
    first = adapter.wrap(
        make_codex_event(CodexEventType.USER, seq=1, payload={"text": "run"})
    )
    dropped = adapter.wrap(
        make_codex_event(
            CodexEventType.TOOL_PROGRESS,
            seq=2,
            item_id="item-1",
        )
    )
    third = adapter.wrap(
        make_codex_event(CodexEventType.INTERRUPTED, seq=3, item_id=None)
    )

    assert dropped is None
    assert first.seq == 1
    assert third.seq == 2


def test_unmapped_capability_event_is_dropped(adapter) -> None:
    event = adapter.wrap(
        make_codex_event(CodexEventType.PLAN_UPDATED, seq=1, payload={})
    )

    assert event is None


def test_route_error_shares_the_session_sequence(adapter) -> None:
    first = adapter.wrap(
        make_codex_event(CodexEventType.USER, seq=1, payload={"text": "run"})
    )
    error = adapter.error_event("transport closed")

    assert first.seq == 1
    assert error.seq == 2
    assert error.type == "error"
    assert error.payload == {
        "subclass": "host_error",
        "errors": ["transport closed"],
    }
