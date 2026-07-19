"""SessionHub — the host-neutral session coordinator (slice-072).

The Hub owns the :class:`BindingStore` (persistence) and routes every action
to the right native host *by binding* (never by guessing the runtime from the
model or UI state — spec C-3). CC sessions live in cc_host's live registry
(reached through the injected ``cc_registry`` + ``cc_opener`` so it stays the
single CC store, spec C-5); Codex sessions live in the shared
:class:`~trowel_py.codex_host.CodexHostManager`.

Invariants enforced here:
* C-1 runtime frozen — :meth:`patch` rejects a runtime change.
* C-2 no cross-resume — :meth:`validate_resume` rejects a native id already
  bound to the other runtime.
* C-3 binding decides routing — every send/interrupt/delete reads the binding.
* resource caps — ``MAX_CONNECTIONS`` counts live sessions, not shared host
  processes (one CodexHostManager serving N threads counts as N, never 1).
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any, Callable

from fastapi import HTTPException, Request

from trowel_py.agent_host.binding import Runtime, SessionBinding, make_binding
from trowel_py.agent_host.cc_adapter import CcEventAdapter
from trowel_py.agent_host.codex_adapter import CodexEventAdapter
from trowel_py.agent_host.schemas import CreateAgentSessionRequest
from trowel_py.agent_host.store import BindingStore
from trowel_py.schemas.agent_host import AgentEvent

_log = logging.getLogger(__name__)

#: Capability tags CC declares this milestone (UI keys off these, never off
#: ``runtime == ...`` — spec C-6).
CC_CAPABILITIES: tuple[str, ...] = ("tools", "approval", "checkpoint", "workflow")
#: Capability tags Codex declares this slice (deeper events land in 074+).
CODEX_CAPABILITIES: tuple[str, ...] = ("tools", "approval")

#: Resource caps — mirror cc_host (slice-028 Q5'). ``MAX_CONNECTIONS`` counts
#: every live session (one CC subprocess or one Codex thread), NOT host
#: processes: the shared CodexHostManager with N threads counts as N. This is
#: the spec pass-criterion "多开上限按活跃连接/运行中 turn 清楚定义，不因共享
#: Codex manager 把多个 thread 算成一个 session".
MAX_CONNECTIONS = 20
MAX_RUNNING = 5

#: Unified event types that end a turn — drives the Codex stream loop's exit
#: (CC ends when ``CCHost.send`` returns, so it does not need this gate). The
#: ``host_status`` / ``host_exited`` case is checked separately: it is a
#: non-terminal type used as a turn terminal when the Codex manager dies
#: (spec §4: never leave the UI on a spinner).
_TURN_TERMINAL_TYPES = frozenset({"finished", "interrupted", "error"})


class RuntimeFrozenError(Exception):
    """A PATCH tried to change a session's runtime (spec C-1)."""


class CrossRuntimeResumeError(Exception):
    """A resume targeted a native id already bound to another runtime (C-2)."""


#: Signature of the CC opener the Hub calls to build + register a CCHost.
#: Production wires this to ``cc_host.routes.open_cc_session``; tests inject a
#: fake so the Hub is exercised without a real CCHost. Kept as a loose
#: ``Callable[..., Any]`` — the two implementations share the
#: ``(req, request, registry) -> OpenedCcSession`` shape but mypy's parameter
#: variance across them adds noise without catching anything real.
CcOpener = Callable[..., Any]


def _default_cc_registry() -> dict[str, Any]:
    """The live CC session registry — cc_host's module-level ``_REGISTRY``."""

    from trowel_py.cc_host import routes as cc_routes

    return cc_routes._REGISTRY  # noqa: SLF001 — shared by design (spec C-5)


def _default_cc_opener() -> CcOpener:
    """The production CC opener: ``cc_host.routes.open_cc_session``."""

    from trowel_py.cc_host import routes as cc_routes

    return cc_routes.open_cc_session


class SessionHub:
    """Owns bindings and routes actions to CCHost or CodexHostManager."""

    def __init__(
        self,
        store: BindingStore,
        codex_manager: Any | None = None,
        *,
        cc_registry: dict[str, Any] | None = None,
        cc_opener: CcOpener | None = None,
    ) -> None:
        """Wire the Hub to its stores + native hosts.

        Args:
            store: the persistence layer for bindings.
            codex_manager: the shared CodexHostManager (``None`` only in tests
                that do not exercise the Codex branch).
            cc_registry: the live CC session dict (defaults to cc_host's
                ``_REGISTRY``). Inject a fresh dict in tests.
            cc_opener: callable that builds + registers a CCHost (defaults to
                ``cc_host.routes.open_cc_session``). Inject a fake in tests.
        """

        self._store = store
        self._codex = codex_manager
        self._cc_registry = (
            cc_registry if cc_registry is not None else _default_cc_registry()
        )
        self._cc_opener = cc_opener if cc_opener is not None else _default_cc_opener()
        self._active_id: str | None = None
        # slice-074: per-session CC + Codex seq adapters (seq spans turns, so
        # each outlives one send). Both own a contiguous per-session counter so
        # dropped events (e.g. Codex assistant_message) don't punch holes the
        # frontend would flag as a gap.
        self._cc_adapters: dict[str, CcEventAdapter] = {}
        self._codex_adapters: dict[str, CodexEventAdapter] = {}

    @property
    def store(self) -> BindingStore:
        """The backing binding store (mainly for route/diagnostic use)."""

        return self._store

    @property
    def codex_available(self) -> bool:
        """True when a CodexHostManager is wired into this Hub."""

        return self._codex is not None

    # ------------------------------------------------------------- create

    def create(
        self, req: CreateAgentSessionRequest, request: Request | None
    ) -> SessionBinding:
        """Create a session under the chosen runtime and persist its binding.

        Raises:
            HTTPException: 400 missing workdir; 409 connection cap reached.
        """

        if not Path(req.workdir).is_dir():
            raise HTTPException(status_code=400, detail="workdir does not exist")
        if self._live_connection_count() >= MAX_CONNECTIONS:
            raise HTTPException(
                status_code=409,
                detail=f"连接数已达上限（{MAX_CONNECTIONS}），请先关闭一些 session",
            )
        if req.runtime == "claude_code":
            return self._create_cc(req, request)
        return self._create_codex(req)

    def _create_cc(
        self, req: CreateAgentSessionRequest, request: Request | None
    ) -> SessionBinding:
        """Open a CC session via cc_host and bind it."""

        from trowel_py.schemas.cc_host import CreateSessionRequest

        cc_req = CreateSessionRequest(
            workdir=req.workdir,
            resume_from=req.resume_from,
            permission_mode=req.permission_mode or "bypassPermissions",
            model=req.model,
            effort=req.effort,
            memory_enabled=req.memory_enabled,
            profile_enabled=req.profile_enabled,
        )
        opened = self._cc_opener(cc_req, request, self._cc_registry)
        binding = make_binding(
            session_id=opened.sid,
            runtime=Runtime.CLAUDE_CODE,
            native_session_id=req.resume_from,
            workdir=req.workdir,
            model=req.model,
            effort=req.effort,
            permission=req.permission_mode,
            memory_enabled=req.memory_enabled,
            profile_enabled=req.profile_enabled,
            capabilities=CC_CAPABILITIES,
            name=opened.name,
        )
        self._store.put(binding)
        self._active_id = opened.sid
        return binding

    def _create_codex(self, req: CreateAgentSessionRequest) -> SessionBinding:
        """Register a Codex thread with the shared manager and bind it.

        ``resume_from`` seeds :attr:`CodexSessionConfig.initial_thread_id` so
        the first send routes through ``thread/resume`` instead of
        ``thread/start`` (spec C-2: resume stays within the originating
        runtime; review HIGH-4).
        """

        if self._codex is None:
            raise HTTPException(status_code=503, detail="codex host unavailable")
        from trowel_py.codex_host import CodexSession, CodexSessionConfig

        sid = uuid.uuid4().hex
        config = CodexSessionConfig(
            trowel_session_id=sid,
            workdir=req.workdir,
            model=req.model,
            effort=req.effort,
            approval_policy=req.approval_policy or "never",
            sandbox=req.sandbox or "read-only",
            initial_thread_id=req.resume_from,
        )
        session = CodexSession(config)
        if self._codex is not None:
            self._codex.register(session)
        binding = make_binding(
            session_id=sid,
            runtime=Runtime.CODEX,
            native_session_id=req.resume_from,
            workdir=req.workdir,
            model=req.model,
            effort=req.effort,
            permission=req.approval_policy or "never",
            memory_enabled=req.memory_enabled,
            profile_enabled=req.profile_enabled,
            capabilities=CODEX_CAPABILITIES,
            name=self._display_name(req.workdir),
        )
        self._store.put(binding)
        self._active_id = sid
        return binding

    def _display_name(self, workdir: str) -> str:
        """Workdir basename + ``#N`` for duplicates, counted across the store."""

        basename = Path(workdir).name or str(workdir)
        same_workdir = sum(
            1 for b in self._store.list_all() if b.workdir == workdir
        )
        return basename if same_workdir == 0 else f"{basename} #{same_workdir + 1}"

    # ------------------------------------------------------------- read

    def get(self, session_id: str) -> SessionBinding | None:
        """Return the binding for ``session_id`` or ``None``."""

        return self._store.get(session_id)

    def _require(self, session_id: str) -> SessionBinding:
        """Look up a binding or raise 404."""

        binding = self._store.get(session_id)
        if binding is None:
            raise HTTPException(
                status_code=404, detail=f"session {session_id} not found"
            )
        return binding

    def list_active(self) -> tuple[list[dict[str, Any]], str | None]:
        """Return every persisted binding as a live-tagged dict + active id.

        Each dict is :meth:`SessionBinding.to_dict` with ``connected`` /
        ``running`` overlaid from the live native host (CCHost attrs or the
        Codex session state) so the UI renders real status, not stale flags.
        """

        items: list[dict[str, Any]] = []
        for binding in self._store.list_all():
            item = binding.to_dict()
            connected, running = self._live_status(binding)
            item["connected"] = connected
            item["running"] = running
            items.append(item)
        return items, self._active_id

    def _live_status(
        self, binding: SessionBinding
    ) -> tuple[bool, bool]:
        """Read (connected, running) from the native host for one binding."""

        if binding.runtime is Runtime.CLAUDE_CODE:
            host = self._cc_registry.get(binding.session_id)
            if host is None:
                return False, False
            return (not getattr(host, "is_dead", True)), bool(
                getattr(host, "running", False)
            )
        if self._codex is None:
            return False, False
        session = self._codex.get_session(binding.session_id)
        if session is None:
            return False, False
        state = getattr(session, "state", None)
        state_value = getattr(state, "value", state)
        return True, state_value == "running"

    # ------------------------------------------------------------- mutate

    def activate(self, session_id: str) -> str:
        """Set the active (viewed) session — view state only (spec C-4).

        For a CC session the legacy cc_host ``_ACTIVE_SID`` is mirrored too,
        so the old ``/api/cc/sessions/active`` and the new agent endpoint
        agree on the active id (review HIGH-3).
        """

        binding = self._require(session_id)
        self._active_id = session_id
        if binding.runtime is Runtime.CLAUDE_CODE:
            from trowel_py.cc_host import routes as cc_routes

            cc_routes._ACTIVE_SID = session_id  # noqa: SLF001 — mirror for legacy /api/cc
        return session_id

    def patch(self, session_id: str, **fields: Any) -> None:
        """Apply a partial update. slice-072 only needs the runtime rejection.

        Raises:
            HTTPException: 404 unknown session.
            RuntimeFrozenError: if ``runtime`` is present and differs.
        """

        binding = self._require(session_id)
        new_runtime = fields.get("runtime")
        if new_runtime is not None and new_runtime != binding.runtime.value:
            raise RuntimeFrozenError(
                f"runtime is frozen at create (C-1): cannot change "
                f"{binding.runtime.value} -> {new_runtime}"
            )
        # Other PATCHable fields (model/effort) follow each host's own contract
        # in later slices; slice-072 intentionally has nothing else to apply.

    def validate_resume(
        self, runtime: Runtime, native_session_id: str | None
    ) -> None:
        """Forbid resuming a native id bound to the other runtime (spec C-2).

        A ``None`` native id is a fresh session — always allowed.

        Raises:
            CrossRuntimeResumeError: if ``native_session_id`` is already bound
                to a session whose runtime differs from ``runtime``.
        """

        if native_session_id is None:
            return
        for binding in self._store.list_all():
            if (
                binding.native_session_id == native_session_id
                and binding.runtime is not runtime
            ):
                raise CrossRuntimeResumeError(
                    f"native session {native_session_id!r} is bound to "
                    f"{binding.runtime.value}; cannot resume as "
                    f"{runtime.value} (C-2)"
                )

    async def interrupt(self, session_id: str) -> None:
        """Interrupt the running turn, routed by binding.

        Raises:
            HTTPException: 404 unknown session or no live native host.
        """

        binding = self._require(session_id)
        if binding.runtime is Runtime.CLAUDE_CODE:
            host = self._cc_registry.get(session_id)
            if host is None:
                raise HTTPException(
                    status_code=404, detail=f"cc session {session_id} not live"
                )
            await host.interrupt()
            return
        if self._codex is None:
            raise HTTPException(status_code=503, detail="codex host unavailable")
        session = self._codex.get_session(session_id)
        if session is None:
            raise HTTPException(
                status_code=404, detail=f"codex session {session_id} not live"
            )
        await self._codex.interrupt(session)

    async def delete(self, session_id: str) -> bool:
        """Close the native host (CC) / drop the thread (Codex) + drop binding.

        Returns ``False`` if the session was unknown (idempotent on the
        binding store; safe to retry).
        """

        binding = self._store.get(session_id)
        if binding is None:
            return False
        if binding.runtime is Runtime.CLAUDE_CODE:
            # Reuse cc_host's closer so the live _REGISTRY + multi-open state
            # (_WORKDIR_INDEX / _SESSION_NAMES / _ACTIVE_SID) stay consistent
            # with /api/cc/* deletions (review HIGH-1).
            from trowel_py.cc_host import routes as cc_routes

            await cc_routes.close_cc_session(session_id, self._cc_registry)
        elif self._codex is not None:
            self._codex.unregister(session_id)
        # slice-074: drop the per-session CC + Codex seq adapters so a reused
        # id starts fresh; the native hosts are closed/unregistered above.
        self._cc_adapters.pop(session_id, None)
        self._codex_adapters.pop(session_id, None)
        self._store.delete(session_id)
        if self._active_id == session_id:
            self._active_id = None
        return True

    async def stream(
        self, session_id: str, text: str
    ) -> AsyncIterator[dict[str, Any]]:
        """Yield unified AgentEvent v1 envelopes from the bound runtime.

        Both runtimes pass through their adapter (CC via the per-session
        :class:`CcEventAdapter`, Codex via the shared :class:`CodexEventAdapter`)
        so every event the client sees is one wire shape. Codex's stream ends
        on a terminal event (finished / interrupted / error / host_exited); CC
        ends when ``CCHost.send`` returns.

        Raises:
            HTTPException: 404 unknown session or no live host; 502 codex turn
                start failure; 503 codex host unavailable.
        """

        binding = self._require(session_id)
        if binding.runtime is Runtime.CLAUDE_CODE:
            host = self._cc_registry.get(session_id)
            if host is None:
                raise HTTPException(
                    status_code=404, detail=f"cc session {session_id} not live"
                )
            adapter = self._cc_adapters.get(session_id)
            if adapter is None:
                adapter = CcEventAdapter(session_id)
                self._cc_adapters[session_id] = adapter
            async for event in host.send(text):
                raw = (
                    dict(event) if isinstance(event, dict) else event.model_dump()
                )
                yield adapter.wrap(raw).model_dump(by_alias=True)
            self._writeback_cc_native(session_id, host)
            return
        if self._codex is None:
            raise HTTPException(status_code=503, detail="codex host unavailable")
        session = self._codex.get_session(session_id)
        if session is None:
            raise HTTPException(
                status_code=404, detail=f"codex session {session_id} not live"
            )
        try:
            await self._codex.send(session, text)
        except HTTPException:
            raise
        except Exception as exc:  # noqa: BLE001 — surface as 502, not 500
            _log.warning("codex turn start failed for %s: %s", session_id, exc)
            raise HTTPException(
                status_code=502, detail=f"codex turn failed: {exc}"
            ) from exc
        adapter = self._codex_adapters.get(session_id)
        if adapter is None:
            adapter = CodexEventAdapter(session_id)
            self._codex_adapters[session_id] = adapter
        async for event in session.events():
            envelope = adapter.wrap(event)
            if envelope is None:
                # adapter dropped a duplicate (assistant_message) or a type with
                # no real producer (tool_progress); the per-session seq is only
                # advanced on emit, so no phantom gap is created.
                continue
            payload = envelope.model_dump(by_alias=True)
            yield payload
            if _is_terminal(payload):
                break
        self._writeback_codex_native(session_id, session)

    def error_envelope(self, session_id: str, detail: Any) -> dict[str, Any]:
        """Build a terminal error envelope from the session's own seq space.

        Route-level failures (the stream raising before/independent of normal
        adapter emission) still need a closing signal, and after slice-074 that
        signal must be a valid AgentEvent sharing the per-session seq allocator
        — a fixed seq=1 would collide with earlier events and be dropped as a
        dup by the frontend (gpt5.6 Critical 1). Falls back to a best-effort
        envelope when the binding is gone (the frontend drops it anyway, since
        ``sessions[sid]`` no longer exists).
        """

        binding = self._store.get(session_id)
        if binding is None:
            return AgentEvent(
                session_id=session_id,
                runtime="claude_code",
                seq=1,
                type="error",
                payload={"subclass": "host_error", "errors": [str(detail)]},
            ).model_dump(by_alias=True)
        if binding.runtime is Runtime.CLAUDE_CODE:
            adapter = self._cc_adapters.get(session_id)
            if adapter is None:
                adapter = CcEventAdapter(session_id)
                self._cc_adapters[session_id] = adapter
        else:
            adapter = self._codex_adapters.get(session_id)
            if adapter is None:
                adapter = CodexEventAdapter(session_id)
                self._codex_adapters[session_id] = adapter
        return adapter.error_event(detail).model_dump(by_alias=True)

    # ------------------------------------------------------------- writeback

    def _writeback_cc_native(self, session_id: str, host: Any) -> None:
        """Record the cc_session_id + effective model once CC reports them."""

        cc_session_id = getattr(host, "cc_session_id", None)
        model = getattr(host, "model", None)
        if cc_session_id is None and model is None:
            return
        try:
            self._store.update_native(
                session_id,
                native_session_id=cc_session_id,
                model=model,
            )
        except KeyError:
            # binding already gone (deleted mid-stream) — nothing to update.
            _log.debug("cc writeback skipped, binding %s gone", session_id)

    def _writeback_codex_native(self, session_id: str, session: Any) -> None:
        """Record the Codex thread id + effective model from the thread binding.

        Skipped when the binding still carries the resume placeholder (empty
        model) so a not-yet-attached session never overwrites a real value
        with the empty stand-in.
        """

        thread_binding = getattr(session, "binding", None)
        if thread_binding is None or not thread_binding.model:
            return
        try:
            self._store.update_native(
                session_id,
                native_session_id=thread_binding.thread_id,
                model=thread_binding.model,
            )
        except KeyError:
            _log.debug("codex writeback skipped, binding %s gone", session_id)

    # ------------------------------------------------------------- internals

    def _live_connection_count(self) -> int:
        """Count live sessions (CC host present + Codex thread registered)."""

        cc_live = sum(
            1 for sid in self._cc_registry if self._store.get(sid) is not None
        )
        codex_live = 0
        if self._codex is not None:
            codex_live = sum(
                1
                for sid in self._codex.session_ids
                if self._store.get(sid) is not None
            )
        return cc_live + codex_live


def _is_terminal(payload: dict[str, Any]) -> bool:
    """True when a unified envelope ends the turn (so the Codex loop can stop).

    ``finished`` / ``interrupted`` / ``error`` are turn terminals. Native Codex
    errors (translator ``_on_error``) are mapped to ``retrying`` by the adapter
    (non-terminal — CodexSession keeps waiting for ``turn/completed``), so they
    never reach this check. The ``host_status`` envelope with
    ``payload.status == "host_exited"`` is also a terminal — it is what a
    running turn observes when the Codex manager dies (spec §4: never leave the
    UI on a spinner).
    """

    event_type = payload.get("type")
    if event_type in _TURN_TERMINAL_TYPES:
        return True
    if event_type == "host_status":
        nested = payload.get("payload")
        if isinstance(nested, dict) and nested.get("status") == "host_exited":
            return True
    return False
