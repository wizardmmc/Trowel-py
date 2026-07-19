"""Shared Codex app-server manager (slice-071).

One :class:`CodexHostManager` owns one app-server process for the whole trowel
backend and routes native notifications to the right
:class:`~trowel_py.codex_host.session.CodexSession` by ``threadId``. Sessions
are cheap bookkeeping; the transport is the expensive shared resource
(spec C-1 — never one app-server per session).

Lifecycle in a sentence: the first ``send`` lazily starts the client, every
notification is routed and translated, and an unexpected EOF flips the manager
to ``degraded`` while every running turn observes a concrete ``host_exited``
terminal event (spec §4 — never leave the UI on a spinner).

The manager is transport-agnostic: it talks to anything that implements the
:class:`~trowel_py.codex_host.transport.AppServerClient` surface. Tests inject
a client wired to :class:`~tests.codex_host._fake.FakeAppServer`; production
uses the real subprocess client.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable, Mapping

from trowel_py.codex_host.errors import ProtocolViolationError
from trowel_py.codex_host.events import (
    CodexEventType,
    HostStatusKind,
    TranslatedItem,
    immutable_payload,
)
from trowel_py.codex_host.session import CodexSession
from trowel_py.codex_host.translator import CodexTranslator
from trowel_py.codex_host.transport import AppServerClient

_log = logging.getLogger(__name__)

# How long ``turn/start`` / ``thread/start`` may take before we give up. Real
# ``thread/start`` is fast (<1s on the spike) but the first call waits on the
# OpenAI login check, so leave plenty of headroom.
_REQUEST_TIMEOUT_S = 60.0


class CodexHostManagerState(str, Enum):
    """Manager lifecycle state (spec §1).

    Transitions::

        stopped --ensure_ready--> starting --ok--> ready
        ready --EOF--> degraded --ensure_ready--> starting
        any --close--> closing --done--> stopped
    """

    STOPPED = "stopped"
    STARTING = "starting"
    READY = "ready"
    DEGRADED = "degraded"
    CLOSING = "closing"


@dataclass(frozen=True)
class OrphanDiagnostic:
    """A notification we could not route to a known session.

    Recorded (not raised) so the manager keeps draining the bus. Aggregated in
    :attr:`CodexHostManager.orphans` for the connection-diagnostics UI and for
    tests (spec §3: orphan events never land on the current UI session).

    Attributes:
        method: The JSON-RPC ``method`` of the notification.
        thread_id: Native thread id when present, else None (global notification).
        turn_id: Native turn id when present, else None.
        reason: Why it was orphaned — ``unknown_thread`` (threadId we have no
            session for), ``unknown_method`` (a method the translator does not
            map and that is not in the explicit ignore list), or
            ``no_thread_id`` (a non-ignored notification with no threadId).
    """

    method: str
    thread_id: str | None
    turn_id: str | None
    reason: str


ClientFactory = Callable[[], AppServerClient]


class CodexHostManager:
    """Owns the shared app-server transport and the thread→session registry."""

    def __init__(
        self,
        *,
        client_factory: ClientFactory | None = None,
        translator: CodexTranslator | None = None,
    ) -> None:
        """Store configuration; the client is created lazily on first use.

        Args:
            client_factory: Builds the :class:`AppServerClient` on demand.
                Production leaves it ``None`` (a default client with the version
                lock on); tests inject one wired to a fake app-server.
            translator: The notification translator. A shared default is fine —
                it is stateless.
        """

        self._client_factory: ClientFactory = client_factory or self._default_client_factory
        self._translator: CodexTranslator = translator or CodexTranslator()
        self._client: AppServerClient | None = None
        self._state: CodexHostManagerState = CodexHostManagerState.STOPPED
        self._sessions: dict[str, CodexSession] = {}
        self._thread_to_session: dict[str, CodexSession] = {}
        self._orphans: list[OrphanDiagnostic] = []
        self._ready_lock: asyncio.Lock = asyncio.Lock()
        self._eof_watcher: asyncio.Task[None] | None = None

    # ------------------------------------------------------------- read-only

    @property
    def state(self) -> CodexHostManagerState:
        """Current manager lifecycle state."""

        return self._state

    @property
    def client(self) -> AppServerClient | None:
        """The shared transport, or None when stopped/degraded."""

        return self._client

    @property
    def orphans(self) -> list[OrphanDiagnostic]:
        """A snapshot copy of recorded orphan diagnostics."""

        return list(self._orphans)

    @property
    def translator(self) -> CodexTranslator:
        """The translator in use (exposed for tests / diagnostics)."""

        return self._translator

    # ------------------------------------------------------- session registry

    def register(self, session: CodexSession) -> None:
        """Add a session to the registry (idempotent on session id).

        Thread binding is recorded separately once ``thread/start`` /
        ``thread/resume`` returns, so a freshly registered session with no
        binding yet simply does not receive notifications until its first send.
        """

        self._sessions[session.session_id] = session

    def get_session(self, session_id: str) -> CodexSession | None:
        """Look up a session by trowel session id."""

        return self._sessions.get(session_id)

    @property
    def session_ids(self) -> tuple[str, ...]:
        """Snapshot of registered trowel session ids (slice-072).

        Read-only view so the host-neutral Session Hub can count live Codex
        sessions without reaching into the private ``_sessions`` dict. Returns
        a tuple (immutable) so a caller cannot mutate the registry through it.
        """

        return tuple(self._sessions.keys())

    def unregister(self, session_id: str) -> CodexSession | None:
        """Drop a session from the registry + its thread route (slice-072).

        Returns the removed session, or None if it was not registered. Used by
        the Session Hub when a Codex session is deleted so the manager stops
        routing notifications to it. The app-server thread itself is NOT
        touched — Codex threads persist server-side; this only drops trowel's
        bookkeeping (an idle thread re-registers on the next resume).
        """

        session = self._sessions.pop(session_id, None)
        if session is not None and session.binding is not None:
            self._thread_to_session.pop(session.binding.thread_id, None)
        return session

    def session_for_thread(self, thread_id: str) -> CodexSession | None:
        """Look up a session by native thread id (the routing direction)."""

        return self._thread_to_session.get(thread_id)

    # ------------------------------------------------------------- lifecycle

    async def ensure_ready(self) -> AppServerClient:
        """Lazily start the shared client, returning it ready to use.

        Concurrent callers serialise on ``_ready_lock``; only the first starts
        the client, the rest observe ``READY`` and return. After an EOF the
        state is ``DEGRADED`` and the client is cleared, so the next
        ``ensure_ready`` restarts a fresh process (spec §4: recovery is
        observable — every session gets a ``READY`` host-status flip).
        """

        async with self._ready_lock:
            if (
                self._state is CodexHostManagerState.READY
                and self._client is not None
                and not self._client.closed
            ):
                return self._client
            self._state = CodexHostManagerState.STARTING
            client = self._client_factory()
            await client.start()
            client.add_notification_listener(self._on_notification)
            self._client = client
            self._state = CodexHostManagerState.READY
            # Restart after degraded: surface a READY flip so the UI can leave
            # its "host degraded" banner (spec §4 — recovery must be visible).
            self._broadcast_host_status(HostStatusKind.READY, reason="ready")
            self._eof_watcher = asyncio.create_task(
                self._eof_watcher_loop(), name="codex-host-eof-watcher"
            )
            return client

    async def close(self) -> None:
        """Tear down the shared client. Safe to call when already stopped."""

        self._state = CodexHostManagerState.CLOSING
        client = self._client
        watcher = self._eof_watcher
        self._eof_watcher = None
        if client is not None:
            await client.close()
        if watcher is not None and not watcher.done():
            watcher.cancel()
            try:
                await watcher
            except asyncio.CancelledError:
                pass
            except Exception:  # noqa: BLE001 — log, do not let close propagate
                _log.debug("eof watcher raised during close", exc_info=True)
        self._client = None
        self._state = CodexHostManagerState.STOPPED

    # ------------------------------------------------------------ turn flow

    async def send(self, session: CodexSession, text: str) -> str:
        """Drive one turn for ``session``: ensure_ready → start/resume → turn/start.

        Returns the native ``turn_id`` (also surfaced via the TURN_STARTED
        event). The caller then drains :meth:`CodexSession.drain` or iterates
        :meth:`CodexSession.events` to consume the stream.

        Raises:
            TurnConflictError: If the session already has a running turn.
            TransportClosedError: If the transport closed mid-send.
        """

        session.begin_send()
        try:
            client = await self.ensure_ready()
            if session.is_new_thread:
                result = await client.request(
                    "thread/start",
                    self._thread_start_params(session),
                    timeout=_REQUEST_TIMEOUT_S,
                )
            else:
                result = await client.request(
                    "thread/resume",
                    self._thread_resume_params(session),
                    timeout=_REQUEST_TIMEOUT_S,
                )
            session.attach_thread_binding(result)
            session.emit_session_started_if_first()
            assert session.binding is not None  # attach_thread_binding just set it
            self._thread_to_session[session.binding.thread_id] = session
            turn_result = await client.request(
                "turn/start",
                self._turn_start_params(
                    session.binding.thread_id, text, session.config.effort
                ),
                timeout=_REQUEST_TIMEOUT_S,
            )
            turn_id = _extract_turn_id(turn_result)
            session.record_turn_started(turn_id, text)
            return turn_id
        except BaseException:
            # The session clears ``_sending`` itself on success (record_turn_started);
            # any failure path must release the reservation or the session is
            # stuck refusing future sends.
            session.abort_send()
            raise

    async def interrupt(self, session: CodexSession) -> None:
        """Send ``turn/interrupt`` for the session's current turn.

        No-op when the session is not running — the manager only forwards the
        request; the terminal state still comes from the native
        ``turn/completed.status`` (spec C-4).
        """

        binding = session.binding
        turn_id = session.current_turn_id
        if binding is None or turn_id is None:
            return
        client = await self.ensure_ready()
        await client.request(
            "turn/interrupt",
            {"threadId": binding.thread_id, "turnId": turn_id},
            timeout=_REQUEST_TIMEOUT_S,
        )

    # ------------------------------------------------------- notification bus

    def _on_notification(self, method: str, params: Mapping[str, Any]) -> None:
        """Sync listener: route → translate → dispatch to the owning session.

        Runs on the transport's reader task, so it must not block. Translation
        and ``put_nowait`` are both synchronous; the queue is unbounded.
        """

        if method in self._translator.ignored_methods:
            return  # capability-gated / echo — drop silently
        thread_id = _extract_thread_id(params)
        if thread_id is None:
            self._record_orphan(method, None, _extract_turn_id_from_params(params), "no_thread_id")
            return
        session = self._thread_to_session.get(thread_id)
        if session is None:
            self._record_orphan(method, thread_id, _extract_turn_id_from_params(params), "unknown_thread")
            return
        try:
            items = self._translator.translate(method, params)
        except ProtocolViolationError as exc:
            # Drift on a mapped method — surface as a structured ERROR rather
            # than swallowing it or killing the reader (spec: never fake success).
            _log.warning("translator rejected %s: %s", method, exc)
            session.emit_translated(
                TranslatedItem(
                    type=CodexEventType.ERROR,
                    thread_id=thread_id,
                    turn_id=_extract_turn_id_from_params(params),
                    payload=immutable_payload(
                        kind="translator_error",
                        method=method,
                        message=str(exc),
                    ),
                )
            )
            return
        if not items:
            # The translator knew the method but mapped it to nothing, and it
            # is not in the explicit ignore list — record so a new method in a
            # future recording is visible instead of silently dropped.
            self._record_orphan(
                method, thread_id, _extract_turn_id_from_params(params), "unknown_method"
            )
            return
        for item in items:
            session.emit_translated(item)

    def _record_orphan(
        self, method: str, thread_id: str | None, turn_id: str | None, reason: str
    ) -> None:
        """Append one orphan diagnostic and log it at debug level."""

        diag = OrphanDiagnostic(
            method=method, thread_id=thread_id, turn_id=turn_id, reason=reason
        )
        self._orphans.append(diag)
        _log.debug(
            "codex orphan notification: method=%s thread=%s turn=%s reason=%s",
            method,
            thread_id,
            turn_id,
            reason,
        )

    # ----------------------------------------------------------- host events

    async def _eof_watcher_loop(self) -> None:
        """Wait for the transport to close, then fan out host-exited.

        Cancelling the client (``close``) sets the event too — that path
        observes ``CLOSING`` and returns without fanning out a degraded signal.
        """

        client = self._client
        if client is None:
            return
        try:
            await client.wait_closed()
        except asyncio.CancelledError:
            return
        except Exception:  # noqa: BLE001 — wait_closed does not raise, but guard anyway
            _log.debug("wait_closed raised", exc_info=True)
            return
        if self._state is CodexHostManagerState.CLOSING:
            return
        await self._on_unexpected_exit(client)

    async def _on_unexpected_exit(self, client: AppServerClient) -> None:
        """Flip to degraded and give every running turn a host-exited event."""

        exit_code = client.last_exit_code
        stderr_tail = client.stderr_tail[:200] if client else ""
        self._state = CodexHostManagerState.DEGRADED
        self._client = None
        self._eof_watcher = None
        reason = "app-server process exited unexpectedly"
        if stderr_tail:
            reason = f"{reason}; stderr={stderr_tail!r}"
        for session in self._sessions.values():
            # Any session with an in-flight turn (RUNNING, or parked in the
            # begin_send → record_turn_started window) gets a concrete
            # HOST_EXITED terminal so the UI is never stuck and the session
            # is not left with ``_sending`` pinned (review H-2).
            if session.has_in_flight_turn:
                session.mark_host_exited(reason, exit_code=exit_code)
            else:
                session.emit_host_status(HostStatusKind.DEGRADED, reason=reason)
        _log.warning("codex host degraded: %s (exit_code=%s)", reason, exit_code)

    def _broadcast_host_status(self, status: HostStatusKind, *, reason: str | None) -> None:
        """Push a non-terminal host-status flip to every registered session."""

        for session in self._sessions.values():
            session.emit_host_status(status, reason=reason)

    # ------------------------------------------------------------- params I/O

    @staticmethod
    def _default_client_factory() -> AppServerClient:
        """Build the production client (version lock on, no recorder by default)."""

        return AppServerClient()

    def _thread_start_params(self, session: CodexSession) -> dict[str, Any]:
        """Build ``thread/start`` params from the session config.

        Field names match ``ThreadStartParams`` in ``v2/thread.rs``. Only the
        keys slice-071 uses are set; ``developerInstructions`` is the raw pipe
        for M/P injection (slice-078 fills it with real content).
        """

        config = session.config
        params: dict[str, Any] = {
            "cwd": config.workdir,
            "approvalPolicy": config.approval_policy,
            "sandbox": config.sandbox,
            "ephemeral": config.ephemeral,
        }
        if config.model is not None:
            params["model"] = config.model
        if config.developer_instructions is not None:
            params["developerInstructions"] = config.developer_instructions
        return params

    def _thread_resume_params(self, session: CodexSession) -> dict[str, Any]:
        """Build ``thread/resume`` params — just the thread id (binding kept)."""

        assert session.binding is not None
        return {"threadId": session.binding.thread_id}

    @staticmethod
    def _turn_start_params(
        thread_id: str, text: str, effort: str | None = None
    ) -> dict[str, Any]:
        """Build ``turn/start`` params for one text user message.

        ``input`` is a ``Vec<UserInput>``; a single ``Text`` element with an
        empty ``text_elements`` list is the minimal valid shape
        (``v2/turn.rs::UserInput::Text``).
        """

        params: dict[str, Any] = {
            "threadId": thread_id,
            "input": [{"type": "text", "text": text, "text_elements": []}],
        }
        if effort is not None:
            params["effort"] = effort
        return params


def _extract_thread_id(params: Mapping[str, Any]) -> str | None:
    """Read the top-level ``threadId`` from a notification.

    Returns None for global notifications (``account/rateLimits/updated`` …)
    and for ``thread/started`` whose id is nested under ``params.thread.id`` —
    that nesting is why ``thread/started`` lives in the translator's ignore
    list rather than the routing path (spec §3 routing note).
    """

    value = params.get("threadId")
    if isinstance(value, str) and value:
        return value
    return None


def _extract_turn_id_from_params(params: Mapping[str, Any]) -> str | None:
    """Best-effort turn id extraction for orphan diagnostics."""

    value = params.get("turnId")
    return value if isinstance(value, str) and value else None


def _extract_turn_id(turn_result: Mapping[str, Any]) -> str:
    """Read ``turn.id`` from a ``turn/start`` response result.

    ``TurnStartResponse`` is ``{ turn: Turn }`` and ``Turn.id`` is the routing
    key for every item/* notification in the turn. Missing it is drift.

    Raises:
        ProtocolViolationError: If the response shape is wrong.
    """

    turn = turn_result.get("turn")
    if not isinstance(turn, Mapping) or not turn.get("id"):
        raise ProtocolViolationError(
            "turn/start response has no turn.id",
            payload=dict(turn_result),
        )
    return str(turn["id"])
