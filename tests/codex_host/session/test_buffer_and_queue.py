from __future__ import annotations

from trowel_py.codex_host.events import (
    CodexEventType,
    TranslatedItem,
    immutable_payload,
)
from trowel_py.codex_host.session import CodexSession, CodexSessionState

from .support import binding_result, running_session, session_config


def test_has_in_flight_turn_covers_pre_record_window() -> None:
    session = CodexSession(session_config())

    assert not session.has_in_flight_turn

    session.begin_send()

    # begin_send 到 record 之间也必须算作进行中，避免 EOF 后遗留发送锁。
    assert session.has_in_flight_turn

    session.attach_thread_binding(binding_result("t-1"))
    session.emit_session_started_if_first()
    session.record_turn_started("turn-1", "hi")

    assert session.has_in_flight_turn

    session.emit_translated(
        TranslatedItem(
            type=CodexEventType.FINISHED,
            thread_id="t-1",
            turn_id="turn-1",
            payload=immutable_payload(status="completed"),
        )
    )

    assert not session.has_in_flight_turn


def test_notifications_before_record_are_buffered_in_order() -> None:
    session = CodexSession(session_config())
    session.begin_send()
    session.attach_thread_binding(binding_result("t-1"))
    session.emit_session_started_if_first()
    delta_item = TranslatedItem(
        type=CodexEventType.ASSISTANT_DELTA,
        thread_id="t-1",
        turn_id="turn-1",
        item_id="m1",
        payload=immutable_payload(delta="hi"),
    )
    finished_item = TranslatedItem(
        type=CodexEventType.FINISHED,
        thread_id="t-1",
        turn_id="turn-1",
        payload=immutable_payload(status="completed"),
    )

    assert session.emit_translated(delta_item) is None
    assert session.emit_translated(finished_item) is None
    assert session.state is CodexSessionState.IDLE

    events = session.record_turn_started("turn-1", "hello")

    assert [event.type for event in events] == [
        CodexEventType.USER,
        CodexEventType.TURN_STARTED,
        CodexEventType.ASSISTANT_DELTA,
        CodexEventType.FINISHED,
    ]
    # 缓冲的 FINISHED 必须在启动事件之后结束 turn，不能复活为 RUNNING。
    assert session.state is CodexSessionState.IDLE
    assert session.current_turn_id is None


def test_seq_monotonic_and_drain_preserves_order() -> None:
    session = running_session()
    session.emit_translated(
        TranslatedItem(
            type=CodexEventType.ASSISTANT_DELTA,
            thread_id="t-1",
            turn_id="turn-1",
            item_id="m1",
            payload=immutable_payload(delta="a"),
        )
    )
    session.emit_translated(
        TranslatedItem(
            type=CodexEventType.ASSISTANT_DELTA,
            thread_id="t-1",
            turn_id="turn-1",
            item_id="m1",
            payload=immutable_payload(delta="b"),
        )
    )

    events = session.drain()
    seqs = [event.seq for event in events]

    assert seqs == list(range(1, len(events) + 1))
    assert [event.type for event in events] == [
        CodexEventType.SESSION_STARTED,
        CodexEventType.USER,
        CodexEventType.TURN_STARTED,
        CodexEventType.ASSISTANT_DELTA,
        CodexEventType.ASSISTANT_DELTA,
    ]
    assert session.drain() == []
