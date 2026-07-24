from __future__ import annotations

from trowel_py.codex_host.events import (
    CodexEventType,
    TranslatedItem,
    immutable_payload,
)
from trowel_py.codex_host.session import CodexSession, CodexSessionState

from .support import binding_result, running_session, session_config


def test_record_turn_started_emits_user_then_turn_started() -> None:
    session = CodexSession(session_config())
    session.begin_send()
    session.attach_thread_binding(binding_result("t-1"))
    session.emit_session_started_if_first()

    events = session.record_turn_started("turn-1", "hello")

    assert [event.type for event in events] == [
        CodexEventType.USER,
        CodexEventType.TURN_STARTED,
    ]
    assert events[0].payload["text"] == "hello"
    assert session.state is CodexSessionState.RUNNING
    assert session.current_turn_id == "turn-1"


def test_finished_flips_to_idle() -> None:
    session = running_session()

    session.emit_translated(
        TranslatedItem(
            type=CodexEventType.FINISHED,
            thread_id="t-1",
            turn_id="turn-1",
            payload=immutable_payload(status="completed"),
        )
    )

    assert session.state is CodexSessionState.IDLE
    assert session.current_turn_id is None


def test_interrupted_flips_to_interrupted() -> None:
    session = running_session()

    session.emit_translated(
        TranslatedItem(
            type=CodexEventType.INTERRUPTED,
            thread_id="t-1",
            turn_id="turn-1",
        )
    )

    assert session.state is CodexSessionState.INTERRUPTED


def test_turn_level_error_flips_to_failed() -> None:
    session = running_session()

    session.emit_translated(
        TranslatedItem(
            type=CodexEventType.ERROR,
            thread_id="t-1",
            turn_id="turn-1",
            payload=immutable_payload(status="failed"),
        )
    )

    assert session.state is CodexSessionState.FAILED


def test_native_error_is_not_terminal() -> None:
    session = running_session()

    # 原生 error 可能重试，只有后续 turn/completed 才能结束 turn。
    session.emit_translated(
        TranslatedItem(
            type=CodexEventType.ERROR,
            thread_id="t-1",
            turn_id="turn-1",
            payload=immutable_payload(kind="native_error", will_retry=True),
        )
    )

    assert session.state is CodexSessionState.RUNNING
    assert session.current_turn_id == "turn-1"


def test_mark_host_exited_pushes_terminal_and_keeps_binding() -> None:
    session = running_session(thread_id="t-9")
    binding_before = session.binding

    event = session.mark_host_exited("app-server exited", exit_code=1)

    assert event.type is CodexEventType.HOST_STATUS
    assert event.payload["status"] == "host_exited"
    assert event.payload["exit_code"] == 1
    assert event.thread_id == "t-9"
    assert session.state is CodexSessionState.FAILED
    assert session.current_turn_id is None
    assert session.binding is binding_before
    assert session.drain()[-1] is event
