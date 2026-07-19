"""Per-trowel-session state for one Codex thread (slice-071).

A :class:`CodexSession` is the cheap object: it owns the binding between one
trowel session id and one native Codex thread/turn, the per-session event
queue and the single-turn state machine. It never touches the app-server
process directly — that stays with :class:`~trowel_py.codex_host.manager.CodexHostManager`,
so many sessions share one transport (spec C-1).

Native facts (model / provider / sandbox / approval policy) come from the
``thread/start`` and ``thread/resume`` response objects — the Rust struct
``ThreadStartResponse`` / ``ThreadResumeResponse`` in
``app-server-protocol/src/protocol/v2/thread.rs`` at 0.144.0. We keep the
sandbox/approval objects as opaque mappings; the UI renders them as "effective
policy" and slice-075/076 will decode them when they gain real fixtures.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass
from enum import Enum
from types import MappingProxyType
from typing import Any, Mapping

from trowel_py.codex_host.errors import CodexHostError, ProtocolViolationError
from trowel_py.codex_host.events import (
    CodexEvent,
    CodexEventType,
    HostStatusKind,
    TranslatedItem,
    host_status_item,
    immutable_payload,
)


class TurnConflictError(CodexHostError):
    """A second ``send`` arrived while the session already has a running turn.

    Spec C-3: a single session runs at most one turn at a time. The second
    caller gets this exception; the in-flight turn is left untouched.
    """


class CodexSessionState(str, Enum):
    """Session-level turn state (spec §1).

    ``WAITING`` is reserved for approval / user-input pauses (slice-075); this
    slice never drives a session into it but keeps the value so the enum
    matches the spec's state list.
    """

    IDLE = "idle"
    RUNNING = "running"
    WAITING = "waiting"
    INTERRUPTED = "interrupted"
    FAILED = "failed"


# States from which a new send is allowed. RUNNING / WAITING are rejected
# (spec C-3). TERMINAL-recoverable states (INTERRUPTED, FAILED) can issue a
# fresh turn — INTERRUPTED is the normal post-interrupt state and FAILED is
# the post-host-exit state, both recoverable on the next send.
_SENDABLE_STATES: frozenset[CodexSessionState] = frozenset(
    {CodexSessionState.IDLE, CodexSessionState.INTERRUPTED, CodexSessionState.FAILED}
)


@dataclass(frozen=True)
class CodexSessionConfig:
    """Frozen inputs that define a Codex session.

    Attributes:
        trowel_session_id: The trowel session id this Codex session is bound to.
        workdir: Absolute working directory the thread runs in.
        model: Optional model override passed to ``thread/start``.
        effort: Optional reasoning effort override (``low`` / ``high`` …).
        developer_instructions: Optional static instructions injected at thread
            start (M/P injection lives in slice-078; this is the raw pipe).
        approval_policy: Codex ``approvalPolicy`` string (``never`` by default
            so slice-071 turns do not stall on approvals — slice-075 overrides).
        sandbox: Codex ``sandbox`` mode (``read-only`` by default).
        ephemeral: Start the thread ``ephemeral`` so it evaporates when the
            app-server process exits — matches the spike's no-residue default.
    """

    trowel_session_id: str
    workdir: str
    model: str | None = None
    effort: str | None = None
    developer_instructions: str | None = None
    approval_policy: str = "never"
    sandbox: str = "read-only"
    ephemeral: bool = True


@dataclass(frozen=True)
class ThreadBinding:
    """The native facts learned from ``thread/start`` / ``thread/resume``.

    Attributes:
        thread_id: The native Codex thread id — the routing key.
        model: Effective model the server selected (authoritative over config).
        model_provider: Effective provider (e.g. ``openai``).
        cwd: Effective working directory the server reported.
        sandbox: Opaque sandbox policy object (rendered as "effective policy").
        approval_policy: Opaque approval policy object.
        service_tier: Optional service tier, when the server reports one.
        reasoning_effort: Optional effective reasoning effort.
    """

    thread_id: str
    model: str
    model_provider: str
    cwd: str
    sandbox: Mapping[str, Any]
    approval_policy: Mapping[str, Any]
    service_tier: str | None = None
    reasoning_effort: str | None = None


def parse_thread_binding(result: Mapping[str, Any]) -> ThreadBinding:
    """Build a :class:`ThreadBinding` from a ``thread/start`` or ``thread/resume`` response result.

    Args:
        result: The ``result`` object of the JSON-RPC response. Must contain
            ``thread.id`` plus the top-level model/provider/cwd/policy fields
            documented by ``ThreadStartResponse`` / ``ThreadResumeResponse``.

    Returns:
        The immutable binding.

    Raises:
        ProtocolViolationError: If the response is missing ``thread.id`` or
            the documented effective-fact fields.
    """

    # Local import: protocol errors live next to the transport to avoid an
    # import cycle (errors <- session <- manager).
    thread = result.get("thread")
    if not isinstance(thread, Mapping) or not thread.get("id"):
        raise ProtocolViolationError(
            "thread/start response has no thread.id",
            payload=dict(result),
        )
    for required in ("model", "modelProvider", "cwd"):
        if required not in result:
            raise ProtocolViolationError(
                f"thread response missing effective fact {required!r}",
                payload=dict(result),
            )
    sandbox = result.get("sandbox")
    approval_policy = result.get("approvalPolicy")
    return ThreadBinding(
        thread_id=str(thread["id"]),
        model=str(result["model"]),
        model_provider=str(result["modelProvider"]),
        cwd=str(result["cwd"]),
        sandbox=MappingProxyType(dict(sandbox)) if isinstance(sandbox, Mapping) else MappingProxyType({}),
        approval_policy=MappingProxyType(dict(approval_policy))
        if isinstance(approval_policy, Mapping)
        else MappingProxyType({}),
        service_tier=str(result["serviceTier"]) if result.get("serviceTier") is not None else None,
        reasoning_effort=str(result["reasoningEffort"])
        if result.get("reasoningEffort") is not None
        else None,
    )


class CodexSession:
    """One trowel session's view of one Codex thread.

    The session is a state machine plus an event queue. The manager drives it:
    it calls :meth:`begin_send` / :meth:`attach_thread_binding` /
    :meth:`record_turn_started` while starting a turn, and
    :meth:`emit_translated` for every notification the manager routes here.
    """

    def __init__(self, config: CodexSessionConfig) -> None:
        """Initialise in IDLE with no binding and an empty event queue."""

        self._config = config
        self._binding: ThreadBinding | None = None
        self._current_turn_id: str | None = None
        self._state: CodexSessionState = CodexSessionState.IDLE
        self._seq: int = 0
        self._session_started_emitted: bool = False
        # Guards against a second send entering before turn_started flips state
        # to RUNNING. asyncio is cooperative so a plain flag is sufficient —
        # there is no await between the check and the set in begin_send.
        self._sending: bool = False
        # True once record_turn_started has stamped USER + TURN_STARTED for the
        # current turn. Notifications routed here between the turn/start
        # response and record_turn_started (same reader batch on a fast turn)
        # are buffered into ``_pending`` and flushed in turn-id order, so a
        # turn/completed arriving before the manager ran record_turn_started
        # cannot flip the state machine out from under the TURN_STARTED event
        # (review H-1).
        self._turn_started: bool = False
        self._pending: list[TranslatedItem] = []
        self._queue: asyncio.Queue[CodexEvent] = asyncio.Queue()

    # ------------------------------------------------------------- read-only

    @property
    def config(self) -> CodexSessionConfig:
        """The frozen session configuration."""

        return self._config

    @property
    def session_id(self) -> str:
        """Shortcut for the trowel session id (the routing identity)."""

        return self._config.trowel_session_id

    @property
    def thread_id(self) -> str | None:
        """The bound native thread id, or None before the first ``thread/start``."""

        return self._binding.thread_id if self._binding is not None else None

    @property
    def binding(self) -> ThreadBinding | None:
        """The effective native facts (None until the first start/resume)."""

        return self._binding

    @property
    def current_turn_id(self) -> str | None:
        """The native turn id currently running, or None when idle."""

        return self._current_turn_id

    @property
    def has_in_flight_turn(self) -> bool:
        """True when a turn has started but not yet reached a terminal state.

        Covers the RUNNING state, the ``begin_send`` → ``record_turn_started``
        window (``_sending`` set, state still IDLE/INTERRUPTED/FAILED), and any
        turn with a live ``current_turn_id``. Used by the manager to decide
        which sessions get a concrete ``HOST_EXITED`` terminal on EOF — without
        this, a session parked in that pre-record window would only get a
        non-terminal DEGRADED and deadlock on its own ``_sending`` flag
        (review H-2).
        """

        return (
            self._sending
            or self._current_turn_id is not None
            or self._state is CodexSessionState.RUNNING
        )

    @property
    def state(self) -> CodexSessionState:
        """The current turn state."""

        return self._state

    @property
    def is_new_thread(self) -> bool:
        """True when no binding exists yet (first send must ``thread/start``)."""

        return self._binding is None

    # ------------------------------------------------------- state machine

    def begin_send(self) -> None:
        """Reserve the session for a new turn.

        Raises:
            TurnConflictError: If a turn is already running or another send is
                mid-flight (spec C-3).
        """

        if self._sending or self._state not in _SENDABLE_STATES:
            raise TurnConflictError(
                f"session {self.session_id} cannot accept a new turn in state "
                f"{self._state.name} (sending={self._sending})"
            )
        self._sending = True
        # New turn: no notifications buffered yet, TURN_STARTED not yet emitted.
        self._turn_started = False
        self._pending = []

    def abort_send(self) -> None:
        """Release the send reservation when the orchestration failed early.

        Called by the manager when ``thread/start`` / ``thread/resume`` /
        ``turn/start`` raised before :meth:`record_turn_started` could clear
        the flag. Without this the session would be stuck refusing every
        future send because ``_sending`` never reset.
        """

        self._sending = False
        self._turn_started = False
        self._pending = []

    def attach_thread_binding(self, result: Mapping[str, Any]) -> ThreadBinding:
        """Record the native facts from a ``thread/start`` / ``thread/resume`` response.

        Called by the manager right after the response comes back, before
        :meth:`record_turn_started`. The binding is overwritten every time —
        the server is authoritative on effective model/policy, and a resume
        after restart may report updated facts.
        """

        binding = parse_thread_binding(result)
        self._binding = binding
        return binding

    def emit_session_started_if_first(self) -> CodexEvent | None:
        """Emit SESSION_STARTED once per session (with effective facts).

        Returns the emitted event, or None if it was already emitted (e.g. a
        resume after restart does not re-fire session_started — the binding is
        refreshed but the session did not "start" again from the UI's view).
        """

        if self._session_started_emitted or self._binding is None:
            return None
        self._session_started_emitted = True
        binding = self._binding
        item = TranslatedItem(
            type=CodexEventType.SESSION_STARTED,
            thread_id=binding.thread_id,
            payload=immutable_payload(
                model=binding.model,
                model_provider=binding.model_provider,
                cwd=binding.cwd,
                service_tier=binding.service_tier,
                reasoning_effort=binding.reasoning_effort,
                sandbox=dict(binding.sandbox),
                approval_policy=dict(binding.approval_policy),
            ),
        )
        return self._emit(item)

    def record_turn_started(self, turn_id: str, user_text: str) -> list[CodexEvent]:
        """Emit the local USER echo + TURN_STARTED, flip to RUNNING.

        The user echo is emitted locally (Codex does not echo the user message
        back as an item notification in the same way CC does) so the UI shows
        the user's own message immediately, stamped with the trowel session id.
        """

        if self._binding is None:
            raise TurnConflictError(
                f"session {self.session_id} cannot start a turn with no thread binding"
            )
        thread_id = self._binding.thread_id
        user_event = self._emit(
            TranslatedItem(
                type=CodexEventType.USER,
                thread_id=thread_id,
                payload=immutable_payload(text=user_text),
            )
        )
        turn_event = self._emit(
            TranslatedItem(
                type=CodexEventType.TURN_STARTED,
                thread_id=thread_id,
                turn_id=turn_id,
                payload=immutable_payload(),
            )
        )
        self._current_turn_id = turn_id
        self._state = CodexSessionState.RUNNING
        self._sending = False
        self._turn_started = True
        # Flush any notifications the reader dispatched between the turn/start
        # response and this call (review H-1) — they now stamp after
        # TURN_STARTED and apply terminal state in the right order.
        flushed: list[CodexEvent] = []
        for pending_item in self._pending:
            flushed.append(self._emit(pending_item))
            self._apply_terminal_state(pending_item)
        self._pending = []
        return [user_event, turn_event, *flushed]

    def emit_translated(self, item: TranslatedItem) -> CodexEvent | None:
        """Stamp, queue and apply state for one translated notification.

        Called by the manager's notification listener after it routed the
        notification to this session by thread id. Returns the queued event,
        or ``None`` when the notification was buffered into the pre-turn
        ``_pending`` list (the reader dispatched it before the manager ran
        :meth:`record_turn_started`); it is flushed in order once the turn is
        recorded (review H-1).
        """

        if self._sending and not self._turn_started:
            self._pending.append(item)
            return None
        event = self._emit(item)
        self._apply_terminal_state(item)
        return event

    def mark_host_exited(self, reason: str, *, exit_code: int | None = None) -> CodexEvent:
        """Synthesise a host-exited terminal event for the running turn.

        Spec §4: on EOF, every running turn ends with a concrete HOST_EXITED
        signal — the UI must never be stuck on a spinner. The binding is kept
        so the next send can resume the same thread after the manager restarts.
        """

        self._sending = False
        self._turn_started = False
        self._pending = []
        running = self._state == CodexSessionState.RUNNING
        self._current_turn_id = None
        self._state = CodexSessionState.FAILED
        return self._emit(
            host_status_item(
                HostStatusKind.HOST_EXITED,
                thread_id=self.thread_id,
                reason=reason,
                exit_code=exit_code,
            ),
            also_terminal=running,
        )

    def emit_host_status(
        self, status: HostStatusKind, *, reason: str | None = None
    ) -> CodexEvent:
        """Emit a non-terminal host status flip (ready / degraded / restarting)."""

        return self._emit(
            host_status_item(status, thread_id=self.thread_id, reason=reason)
        )

    # --------------------------------------------------------------- events

    def drain(self) -> list[CodexEvent]:
        """Non-blocking: pull every event currently in the queue.

        Tests use this to assert ordering and contents without awaiting.
        Production consumers use :meth:`events` instead.
        """

        out: list[CodexEvent] = []
        while not self._queue.empty():
            out.append(self._queue.get_nowait())
        return out

    async def events(self) -> AsyncIterator[CodexEvent]:
        """Yield events forever, in order.

        Each session owns its own queue, so streaming one session never blocks
        another (spec §1: multiple threads concurrently). The generator runs
        until the session is discarded.
        """

        while True:
            event = await self._queue.get()
            yield event

    # ------------------------------------------------------------- internals

    def _next_seq(self) -> int:
        """Return the next per-session sequence number (starts at 1)."""

        self._seq += 1
        return self._seq

    def _stamp(self, item: TranslatedItem) -> CodexEvent:
        """Attach session id + seq to a translated item."""

        return CodexEvent(
            session_id=self._config.trowel_session_id,
            seq=self._next_seq(),
            type=item.type,
            thread_id=item.thread_id,
            turn_id=item.turn_id,
            item_id=item.item_id,
            payload=item.payload,
        )

    def _emit(self, item: TranslatedItem, *, also_terminal: bool = False) -> CodexEvent:
        """Stamp, queue and return one event."""

        event = self._stamp(item)
        self._queue.put_nowait(event)
        return event

    def _apply_terminal_state(self, item: TranslatedItem) -> None:
        """Flip state machine on FINISHED / INTERRUPTED / turn-level ERROR.

        Native ``error`` notifications (``payload.kind == "native_error"``) are
        NOT terminal here — they surface the failure to the UI but the turn is
        only ended by ``turn/completed``. That avoids killing a turn that the
        app-server is still retrying (``will_retry=True``).
        """

        if item.type is CodexEventType.FINISHED:
            self._current_turn_id = None
            self._state = CodexSessionState.IDLE
        elif item.type is CodexEventType.INTERRUPTED:
            self._current_turn_id = None
            self._state = CodexSessionState.INTERRUPTED
        elif item.type is CodexEventType.ERROR:
            if item.payload.get("kind") == "native_error":
                return
            self._current_turn_id = None
            self._state = CodexSessionState.FAILED
