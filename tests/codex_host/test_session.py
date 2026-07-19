"""CodexSession state-machine unit tests (no app-server, pure state + queue).

Drives the session through the same call sequence the manager uses, so any
state-machine regression surfaces without spinning a fake transport.
"""

from __future__ import annotations

import pytest

from trowel_py.codex_host.errors import ProtocolViolationError
from trowel_py.codex_host.events import (
    CodexEventType,
    TranslatedItem,
    immutable_payload,
)
from trowel_py.codex_host.session import (
    CodexSession,
    CodexSessionConfig,
    CodexSessionState,
    TurnConflictError,
    parse_thread_binding,
)


def _config(sid: str = "s1") -> CodexSessionConfig:
    """Minimal valid config for tests."""

    return CodexSessionConfig(trowel_session_id=sid, workdir="/tmp/trowel-test")


def test_normal_session_persists_rollout_for_future_resume() -> None:
    """A trowel-managed thread must survive an app-server restart by default."""

    assert _config().ephemeral is False


def _binding_result(tid: str = "t-1") -> dict:
    """A ``thread/start`` response result with the documented effective facts."""

    return {
        "thread": {"id": tid},
        "model": "gpt-5.6-sol",
        "modelProvider": "openai",
        "cwd": "/tmp/trowel-test",
        "sandbox": {"mode": "read-only"},
        "approvalPolicy": {"policy": "never"},
        "serviceTier": None,
        "reasoningEffort": "high",
    }


def _drive_to_running(
    sid: str = "s1", tid: str = "t-1", text: str = "hi"
) -> CodexSession:
    """Run a session through begin_send → binding → turn_started (state=RUNNING)."""

    session = CodexSession(_config(sid))
    session.begin_send()
    session.attach_thread_binding(_binding_result(tid))
    session.emit_session_started_if_first()
    session.record_turn_started("turn-1", text)
    return session


# ----------------------------------------------------------------- begin_send


def test_begin_send_allowed_from_idle() -> None:
    """IDLE accepts a new send reservation."""

    session = CodexSession(_config())
    session.begin_send()
    assert session.state is CodexSessionState.IDLE


def test_second_begin_send_while_sending_rejected() -> None:
    """Spec C-3: a concurrent send before turn_started is rejected."""

    session = CodexSession(_config())
    session.begin_send()
    with pytest.raises(TurnConflictError):
        session.begin_send()


def test_begin_send_rejected_while_running() -> None:
    """A turn already in progress blocks a second send."""

    session = _drive_to_running()
    with pytest.raises(TurnConflictError):
        session.begin_send()


def test_abort_send_releases_reservation() -> None:
    """An aborted send lets the next send proceed (manager failure path)."""

    session = CodexSession(_config())
    session.begin_send()
    session.abort_send()
    session.begin_send()  # no raise


def test_failed_turn_start_keeps_pending_model_effort_pair() -> None:
    """A rejected native request must not lose or partially commit selection."""

    session = CodexSession(_config())
    session.attach_thread_binding(_binding_result())
    session.queue_turn_settings("gpt-5.6-luna", "medium")
    session.begin_send()
    assert session.next_turn_settings() == ("gpt-5.6-luna", "medium")
    session.abort_send()
    assert session.next_turn_settings() == ("gpt-5.6-luna", "medium")
    assert session.binding is not None
    assert session.binding.model == "gpt-5.6-sol"


def test_accepted_turn_commits_model_effort_together() -> None:
    """Only an accepted turn moves both effective settings and emits one event."""

    session = CodexSession(_config())
    session.attach_thread_binding(_binding_result())
    session.queue_turn_settings("gpt-5.6-luna", "medium")
    event = session.commit_turn_settings(model="gpt-5.6-luna", effort="medium")
    assert event is not None
    assert event.type is CodexEventType.MODEL_CHANGED
    assert session.binding is not None
    assert (session.binding.model, session.binding.reasoning_effort) == (
        "gpt-5.6-luna",
        "medium",
    )


# -------------------------------------------------------- binding + session


def test_parse_thread_binding_reads_effective_facts() -> None:
    """The binding captures model/provider/cwd/policy from the response."""

    binding = parse_thread_binding(_binding_result("t-7"))
    assert binding.thread_id == "t-7"
    assert binding.model == "gpt-5.6-sol"
    assert binding.model_provider == "openai"
    assert binding.reasoning_effort == "high"
    assert dict(binding.sandbox) == {"mode": "read-only"}


def test_parse_thread_binding_reads_real_0144_permission_shape() -> None:
    """Real 0.144.0 strings/profile/network facts remain separately visible."""

    binding = parse_thread_binding(
        {
            "thread": {"id": "t-real"},
            "model": "gpt-5.6-sol",
            "modelProvider": "openai",
            "cwd": "/tmp/trowel-test",
            "sandbox": {"type": "readOnly", "networkAccess": False},
            "approvalPolicy": "on-request",
            "activePermissionProfile": {"id": ":read-only", "extends": None},
            "reasoningEffort": "high",
        }
    )
    assert binding.effective_sandbox == "read-only"
    assert binding.effective_approval == "on-request"
    assert binding.permission_profile == ":read-only"
    assert binding.network_access is False


def test_parse_thread_binding_missing_model_raises() -> None:
    """Drift in the response (no model) is structural, not tolerable."""

    with pytest.raises(ProtocolViolationError):
        parse_thread_binding(
            {"thread": {"id": "t"}, "modelProvider": "openai", "cwd": "/x"}
        )


def test_parse_thread_binding_missing_thread_id_raises() -> None:
    """No thread id = no routing key = fail loudly."""

    with pytest.raises(ProtocolViolationError):
        parse_thread_binding({"model": "m", "modelProvider": "openai", "cwd": "/x"})


def test_session_started_emitted_once_with_facts() -> None:
    """SESSION_STARTED fires exactly once and carries the effective facts."""

    session = CodexSession(_config())
    session.begin_send()
    session.attach_thread_binding(_binding_result("t-1"))
    first = session.emit_session_started_if_first()
    second = session.emit_session_started_if_first()
    assert first is not None and first.type is CodexEventType.SESSION_STARTED
    assert first.payload["model"] == "gpt-5.6-sol"
    assert first.thread_id == "t-1"
    assert second is None


# ------------------------------------------------------------ record_turn


def test_record_turn_started_emits_user_then_turn_started() -> None:
    """USER echo precedes TURN_STARTED; state flips to RUNNING."""

    session = CodexSession(_config())
    session.begin_send()
    session.attach_thread_binding(_binding_result("t-1"))
    session.emit_session_started_if_first()
    events = session.record_turn_started("turn-1", "hello")
    assert [e.type for e in events] == [
        CodexEventType.USER,
        CodexEventType.TURN_STARTED,
    ]
    assert events[0].payload["text"] == "hello"
    assert session.state is CodexSessionState.RUNNING
    assert session.current_turn_id == "turn-1"


# ----------------------------------------------------------- terminal flip


def test_finished_flips_to_idle() -> None:
    """turn/completed(completed) → FINISHED → IDLE."""

    session = _drive_to_running()
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
    """turn/completed(interrupted) → INTERRUPTED."""

    session = _drive_to_running()
    session.emit_translated(
        TranslatedItem(
            type=CodexEventType.INTERRUPTED, thread_id="t-1", turn_id="turn-1"
        )
    )
    assert session.state is CodexSessionState.INTERRUPTED


def test_turn_level_error_flips_to_failed() -> None:
    """turn/completed(failed) → ERROR without native_error kind → FAILED."""

    session = _drive_to_running()
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
    """A native ``error`` notification (will_retry) must not kill the turn."""

    session = _drive_to_running()
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


# --------------------------------------------------------------- host exit


def test_mark_host_exited_pushes_terminal_and_keeps_binding() -> None:
    """Spec §4: running turn ends with HOST_EXITED; binding retained for resume."""

    session = _drive_to_running(tid="t-9")
    binding_before = session.binding
    event = session.mark_host_exited("app-server exited", exit_code=1)
    assert event.type is CodexEventType.HOST_STATUS
    assert event.payload["status"] == "host_exited"
    assert event.payload["exit_code"] == 1
    assert event.thread_id == "t-9"
    assert session.state is CodexSessionState.FAILED
    assert session.current_turn_id is None
    assert session.binding is binding_before  # not cleared


def test_has_in_flight_turn_covers_pre_record_window() -> None:
    """H-2: the begin_send → record window counts as in-flight.

    Without this, a session parked there on EOF would only get a non-terminal
    DEGRADED and deadlock on its own ``_sending`` flag.
    """

    session = CodexSession(_config())
    assert not session.has_in_flight_turn
    session.begin_send()
    assert session.has_in_flight_turn  # _sending pinned, state still IDLE
    session.attach_thread_binding(_binding_result("t-1"))
    session.emit_session_started_if_first()
    session.record_turn_started("turn-1", "hi")
    assert session.has_in_flight_turn  # RUNNING
    session.emit_translated(
        TranslatedItem(
            type=CodexEventType.FINISHED,
            thread_id="t-1",
            turn_id="turn-1",
            payload=immutable_payload(status="completed"),
        )
    )
    assert not session.has_in_flight_turn  # back to IDLE


def test_notifications_before_record_are_buffered_in_order() -> None:
    """H-1 guard: notifications between turn/start response and record_turn_started
    are buffered, flushed after TURN_STARTED — a turn/completed arriving first
    cannot flip the state machine before the turn is recorded (no revival)."""

    session = CodexSession(_config())
    session.begin_send()
    session.attach_thread_binding(_binding_result("t-1"))
    session.emit_session_started_if_first()

    # The reader dispatches a delta + turn/completed before the manager task
    # runs record_turn_started. Both must buffer (return None), not emit.
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
    # State must not have flipped on the buffered FINISHED.
    assert session.state is CodexSessionState.IDLE

    events = session.record_turn_started("turn-1", "hello")
    assert [e.type for e in events] == [
        CodexEventType.USER,
        CodexEventType.TURN_STARTED,
        CodexEventType.ASSISTANT_DELTA,
        CodexEventType.FINISHED,
    ]
    # The flushed FINISHED ends the turn — no RUNNING revival.
    assert session.state is CodexSessionState.IDLE
    assert session.current_turn_id is None


def test_failed_session_can_send_again() -> None:
    """A host-exited session is sendable again (manager will resume)."""

    session = _drive_to_running()
    session.mark_host_exited("eof")
    session.begin_send()  # FAILED is in the sendable set


# --------------------------------------------------------- seq + queue


def test_seq_monotonic_and_drain_preserves_order() -> None:
    """Per-session seq strictly increases; drain preserves emit order."""

    session = _drive_to_running()
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
    seqs = [e.seq for e in events]
    assert seqs == sorted(seqs)
    assert len(seqs) == len(set(seqs))  # no dupes
    # SESSION_STARTED < USER < TURN_STARTED < delta(a) < delta(b)
    types = [e.type for e in events]
    assert types == [
        CodexEventType.SESSION_STARTED,
        CodexEventType.USER,
        CodexEventType.TURN_STARTED,
        CodexEventType.ASSISTANT_DELTA,
        CodexEventType.ASSISTANT_DELTA,
    ]
