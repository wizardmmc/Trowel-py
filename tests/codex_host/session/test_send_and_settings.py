from __future__ import annotations

import pytest

from trowel_py.codex_host.events import CodexEventType
from trowel_py.codex_host.session import (
    CodexSession,
    CodexSessionState,
    TurnConflictError,
)

from .support import binding_result, running_session, session_config


def test_begin_send_allowed_from_idle() -> None:
    session = CodexSession(session_config())

    session.begin_send()

    assert session.state is CodexSessionState.IDLE


def test_second_begin_send_while_sending_rejected() -> None:
    session = CodexSession(session_config())
    session.begin_send()

    with pytest.raises(TurnConflictError):
        session.begin_send()


def test_begin_send_rejected_while_running() -> None:
    session = running_session()

    with pytest.raises(TurnConflictError):
        session.begin_send()


def test_abort_send_releases_reservation() -> None:
    session = CodexSession(session_config())
    session.begin_send()

    assert session.has_in_flight_turn

    session.abort_send()

    assert not session.has_in_flight_turn

    session.begin_send()


def test_failed_turn_start_keeps_pending_model_effort_pair() -> None:
    session = CodexSession(session_config())
    session.attach_thread_binding(binding_result())
    session.queue_turn_settings("gpt-5.6-luna", "medium")
    session.begin_send()

    assert session.next_turn_settings() == ("gpt-5.6-luna", "medium")

    session.abort_send()

    assert session.next_turn_settings() == ("gpt-5.6-luna", "medium")
    assert session.binding is not None
    assert session.binding.model == "gpt-5.6-sol"


def test_commit_turn_settings_updates_pair_and_emits_event() -> None:
    session = CodexSession(session_config())
    session.attach_thread_binding(binding_result())
    session.queue_turn_settings("gpt-5.6-luna", "medium")

    event = session.commit_turn_settings(model="gpt-5.6-luna", effort="medium")

    assert event is not None
    assert event.type is CodexEventType.MODEL_CHANGED
    assert session.binding is not None
    assert (session.binding.model, session.binding.reasoning_effort) == (
        "gpt-5.6-luna",
        "medium",
    )
    assert session.next_turn_settings() == (None, None)
    assert session.drain() == [event]


def test_failed_session_can_send_again() -> None:
    session = running_session()
    session.mark_host_exited("eof")

    session.begin_send()

    assert session.has_in_flight_turn
