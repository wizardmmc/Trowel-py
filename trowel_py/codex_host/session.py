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
from dataclasses import dataclass, replace
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
from trowel_py.codex_host.protocol import TROWEL_NOTE_SEARCH_SERVER_NAME


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
class TrowelMemoryMcpConfig:
    """Config for the trowel memory MCP server attached to a Codex thread.

    slice-078: Codex's app-server spawns MCP servers itself (unlike cc, which
    trowel spawns via ``--mcp-config``). The full server definition rides on
    the ``thread/start`` / ``thread/resume`` request under
    ``config.mcp_servers.<name>``, including the env that carries host-neutral
    identity (``TROWEL_HOST_KIND`` / ``TROWEL_NATIVE_SESSION_ID`` /
    ``MEMORY_ROOT``).

    Identity stamping (codex review HIGH-3): ``TROWEL_NATIVE_SESSION_ID`` is
    left EMPTY on a fresh ``thread/start`` — the native thread_id is only
    learned from the response, and the MCP server's env freezes mid-request
    before that. Stamping it with the trowel session id would mislabel the
    access-log row (the field's contract is the Codex thread_id). On
    ``thread/resume`` the binding already knows the thread_id, so it is
    stamped for real. Codex access-log rows for a fresh memory-on session
    therefore carry ``host_kind='codex'`` and ``native_session_id=''`` until
    the session is resumed after a restart; cross-reference the trowel
    session id (always present) to find the binding.

    Attributes:
        server_name: The MCP server name. Defaults to
            :data:`TROWEL_NOTE_SEARCH_SERVER_NAME` — specific enough to avoid
            colliding with user-configured MCP servers in ``~/.codex/config.toml``.
        command: Executable path (``sys.executable`` in production).
        module_args: argv tail that spawns ``trowel_py.memory.mcp_server``.
        memory_root: Absolute path to the memory tree (``MEMORY_ROOT`` env).
        trowel_session_id: trowel's session id — the always-present routing
            identity. NOT used as ``native_session_id`` (see stamping note).
    """

    server_name: str
    command: str
    module_args: tuple[str, ...]
    memory_root: str
    trowel_session_id: str

    def to_thread_config(self, *, native_session_id: str = "") -> dict[str, Any]:
        """Build the ``config.mcp_servers`` object for ``thread/start`` / ``resume``.

        Args:
            native_session_id: The value to stamp as ``TROWEL_NATIVE_SESSION_ID``.
                Empty (default) on a fresh ``thread/start`` — the thread_id is
                unknown until the response. On ``thread/resume`` pass the real
                thread_id from the binding so the MCP writes honest identity.

        Returns:
            The mapping to put under ``params.config.mcp_servers``. The server
            is ``required`` (a startup failure must surface, not silently drop
            the read-path), all three tools are enabled, and approval is
            pre-granted so the model can call search/read/outcome without
            per-call confirmation — matching the cc path, where the memory MCP
            is a trusted local server with no per-call approval gate.
        """

        return {
            self.server_name: {
                "command": self.command,
                "args": list(self.module_args),
                "env": {
                    "MEMORY_ROOT": self.memory_root,
                    "TROWEL_SESSION_ID": self.trowel_session_id,
                    "TROWEL_HOST_KIND": "codex",
                    "TROWEL_NATIVE_SESSION_ID": native_session_id,
                },
                "required": True,
                "enabled_tools": ["search", "read", "outcome"],
                "default_tools_approval_mode": "approve",
            }
        }


def build_default_trowel_memory_mcp(
    *,
    trowel_session_id: str,
    memory_root: str,
    server_name: str = TROWEL_NOTE_SEARCH_SERVER_NAME,
) -> TrowelMemoryMcpConfig:
    """Build the standard trowel memory MCP config for a Codex thread.

    Args:
        trowel_session_id: The trowel session id this MCP server is owned by.
        memory_root: Absolute path to the memory tree.
        server_name: Override the server name (default
            :data:`TROWEL_NOTE_SEARCH_SERVER_NAME`). Tests use this to exercise
            isolation logic without touching the real default.

    Returns:
        The frozen config; ``command``/``module_args`` point at the current
        interpreter running ``trowel_py.memory.mcp_server``.
    """

    import sys

    return TrowelMemoryMcpConfig(
        server_name=server_name,
        command=sys.executable,
        module_args=("-m", "trowel_py.memory.mcp_server"),
        memory_root=str(memory_root),
        trowel_session_id=trowel_session_id,
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
            NB: codex applies this with ``developer_instructions.or(cfg.developer_instructions)``
            (core/src/config/mod.rs) — passing it **overrides** any user-configured
            ``developer_instructions`` from ``~/.codex/config.toml`` (it does NOT
            override codex's own ``base_instructions``). This is an intentional
            takeover for the experiment: trowel owns the developer channel when
            M/P injection is on. CC's ``--append-system-prompt`` is genuinely
            append-only; the codex path is NOT, by upstream design.
        approval_policy: Optional Codex ``approvalPolicy`` override. ``None``
            means follow the app-server's effective configuration.
        sandbox: Optional Codex ``sandbox`` override. ``None`` means follow.
        ephemeral: Whether to skip persisting the native rollout. Normal trowel
            sessions default to ``False`` because their saved thread binding
            must remain resumable after an app-server restart. Isolated smoke
            tests may opt into ``True`` explicitly.
        trowel_memory_mcp: slice-078 — the trowel memory MCP server to attach
            on ``thread/start`` under ``config.mcp_servers``. ``None`` on a
            memory-off session (the whole read-path is closed, C-3). Carries
            the host-neutral identity env the spawned MCP server reads.
    """

    trowel_session_id: str
    workdir: str
    model: str | None = None
    effort: str | None = None
    developer_instructions: str | None = None
    approval_policy: str | None = None
    sandbox: str | None = None
    ephemeral: bool = False
    initial_thread_id: str | None = None
    trowel_memory_mcp: TrowelMemoryMcpConfig | None = None


@dataclass(frozen=True)
class ThreadBinding:
    """The native facts learned from ``thread/start`` / ``thread/resume``.

    Attributes:
        thread_id: The native Codex thread id — the routing key.
        model: Effective model the server selected (authoritative over config).
        model_provider: Effective provider (e.g. ``openai``).
        cwd: Effective working directory the server reported.
        sandbox: Opaque sandbox policy object (rendered as "effective policy").
        approval_policy: Raw approval policy returned by app-server.
        permission_profile: Effective named profile id when reported.
        effective_sandbox: Normalized sandbox mode for public display.
        effective_approval: Normalized approval policy for public display.
        network_access: Effective network fact, or None when native omitted it.
        service_tier: Optional service tier, when the server reports one.
        reasoning_effort: Optional effective reasoning effort.
    """

    thread_id: str
    model: str
    model_provider: str
    cwd: str
    sandbox: Mapping[str, Any]
    approval_policy: str | Mapping[str, Any] | None
    permission_profile: str | None = None
    effective_sandbox: str | None = None
    effective_approval: str | None = None
    network_access: bool | None = None
    service_tier: str | None = None
    reasoning_effort: str | None = None


def _wire_mode(value: object) -> str | None:
    """Normalize a camelCase app-server mode without guessing unknown values."""

    if not isinstance(value, str) or not value:
        return None
    known = {
        "readOnly": "read-only",
        "workspaceWrite": "workspace-write",
        "dangerFullAccess": "danger-full-access",
        "externalSandbox": "external-sandbox",
    }
    return known.get(value, value)


def _sandbox_facts(value: object) -> tuple[str | None, bool | None]:
    """Extract sandbox mode and network access from the native policy object."""

    if not isinstance(value, Mapping):
        return None, None
    mode = _wire_mode(value.get("type") or value.get("mode"))
    raw_network = value.get("networkAccess")
    if isinstance(raw_network, bool):
        network = raw_network
    elif raw_network == "enabled":
        network = True
    elif raw_network == "restricted":
        network = False
    elif mode == "danger-full-access":
        # The 0.144.0 DangerFullAccess variant has no networkAccess field; the
        # protocol variant itself is the explicit unrestricted fact.
        network = True
    else:
        network = None
    return mode, network


def _approval_fact(value: object) -> str | None:
    """Extract the effective approval policy from new or legacy wire shapes."""

    if isinstance(value, str):
        return value
    if isinstance(value, Mapping) and isinstance(value.get("policy"), str):
        return str(value["policy"])
    return None


def _permission_profile_fact(value: object) -> str | None:
    """Extract ``activePermissionProfile.id`` when app-server reports it."""

    if isinstance(value, Mapping) and isinstance(value.get("id"), str):
        return str(value["id"])
    return None


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
    effective_sandbox, network_access = _sandbox_facts(sandbox)
    effective_approval = _approval_fact(approval_policy)
    permission_profile = _permission_profile_fact(result.get("activePermissionProfile"))
    return ThreadBinding(
        thread_id=str(thread["id"]),
        model=str(result["model"]),
        model_provider=str(result["modelProvider"]),
        cwd=str(result["cwd"]),
        sandbox=MappingProxyType(dict(sandbox))
        if isinstance(sandbox, Mapping)
        else MappingProxyType({}),
        approval_policy=(
            MappingProxyType(dict(approval_policy))
            if isinstance(approval_policy, Mapping)
            else str(approval_policy)
            if isinstance(approval_policy, str)
            else None
        ),
        permission_profile=permission_profile,
        effective_sandbox=effective_sandbox,
        effective_approval=effective_approval,
        network_access=network_access,
        service_tier=str(result["serviceTier"])
        if result.get("serviceTier") is not None
        else None,
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
        # slice-072: a resumed thread starts with a minimal placeholder binding
        # so is_new_thread is False and manager.send routes through
        # thread/resume; attach_thread_binding overwrites it with the real
        # ThreadBinding once the resume response arrives.
        self._binding: ThreadBinding | None
        if config.initial_thread_id is not None:
            self._binding = ThreadBinding(
                thread_id=config.initial_thread_id,
                model="",
                model_provider="",
                cwd=config.workdir,
                sandbox=MappingProxyType({}),
                approval_policy=None,
            )
        else:
            self._binding = None
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
        self._has_started_turn: bool = False
        self._pending: list[TranslatedItem] = []
        self._queue: asyncio.Queue[CodexEvent] = asyncio.Queue()
        self._pending_turn_settings: tuple[str, str] | None = None

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

    def queue_turn_settings(self, model: str, effort: str) -> None:
        """Stage an idle-only model/effort pair for the next native turn.

        Args:
            model: Native model id from the current app-server catalog.
            effort: Native reasoning-effort value supported by that model.

        Raises:
            TurnConflictError: If a turn is running or a send is being started.
        """

        if self._sending or self._state not in _SENDABLE_STATES:
            raise TurnConflictError(
                f"session {self.session_id} cannot change settings in state "
                f"{self._state.name}"
            )
        self._pending_turn_settings = (model, effort)

    def next_turn_settings(self) -> tuple[str | None, str | None]:
        """Return the atomic settings pair to put on the next ``turn/start``."""

        if self._pending_turn_settings is not None:
            return self._pending_turn_settings
        if not self._has_started_turn:
            return self._config.model, self._config.effort
        return None, None

    def commit_turn_settings(
        self, *, model: str | None, effort: str | None
    ) -> CodexEvent | None:
        """Commit settings only after app-server accepted ``turn/start``.

        Args:
            model: Model sent on the accepted native request, if any.
            effort: Effort sent on the accepted native request, if any.

        Returns:
            A ``model_changed`` event when either setting was applied.
        """

        if self._binding is None or (model is None and effort is None):
            return None
        self._binding = replace(
            self._binding,
            model=model if model is not None else self._binding.model,
            reasoning_effort=(
                effort if effort is not None else self._binding.reasoning_effort
            ),
        )
        self._pending_turn_settings = None
        return self._emit(
            TranslatedItem(
                type=CodexEventType.MODEL_CHANGED,
                thread_id=self._binding.thread_id,
                payload=immutable_payload(
                    model=self._binding.model,
                    effort=self._binding.reasoning_effort,
                ),
            )
        )

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
                approval_policy=(
                    dict(binding.approval_policy)
                    if isinstance(binding.approval_policy, Mapping)
                    else binding.approval_policy
                ),
                permission_profile=binding.permission_profile,
                effective_sandbox=binding.effective_sandbox,
                effective_approval=binding.effective_approval,
                network_access=binding.network_access,
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
        self._has_started_turn = True
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

    def mark_host_exited(
        self, reason: str, *, exit_code: int | None = None
    ) -> CodexEvent:
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
