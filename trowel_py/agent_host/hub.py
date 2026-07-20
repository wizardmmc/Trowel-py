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

import hashlib
import logging
import uuid
from collections.abc import AsyncIterator
from datetime import date
from pathlib import Path
from typing import Any, Callable

from fastapi import HTTPException, Request

from trowel_py.agent_host.binding import Runtime, SessionBinding, make_binding
from trowel_py.agent_host.cc_adapter import CcEventAdapter
from trowel_py.agent_host.codex_adapter import CodexEventAdapter
from trowel_py.agent_host.schemas import CreateAgentSessionRequest
from trowel_py.codex_host.pending_requests import (
    PendingRequestConflictError,
    PendingRequestDecisionError,
    PendingRequestNotFoundError,
    PendingRequestOwnershipError,
)
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

_CODEX_PERMISSION_PRESETS: dict[str, tuple[str | None, str | None]] = {
    "follow": (None, None),
    "read-only": ("on-request", "read-only"),
    "workspace-write": ("on-request", "workspace-write"),
    "danger-full-access": ("never", "danger-full-access"),
}


class RuntimeFrozenError(Exception):
    """A PATCH tried to change a session's runtime (spec C-1)."""


class CrossRuntimeResumeError(Exception):
    """A resume targeted a native id already bound to another runtime (C-2)."""


class ConditionMismatchError(Exception):
    """A resume kept the same native id but flipped the M/P condition (C-2).

    slice-078: the M/P switches are frozen at create. Resuming the same native
    thread with a different memory_enabled / profile_enabled would silently
    swap the experiment condition (e.g. a memory-on thread resumed as
    memory-off), so the hub refuses and the caller must create a new session.
    """


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
        codex_config_home: str | Path | None = None,
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
            codex_config_home: slice-078 override for the codex config dir used
                by the memory-MCP isolation check (``~/.codex`` by default, or
                ``$CODEX_HOME``). Tests inject a tmp dir; production leaves it
                ``None``.
        """

        self._store = store
        self._codex = codex_manager
        self._cc_registry = (
            cc_registry if cc_registry is not None else _default_cc_registry()
        )
        self._cc_opener = cc_opener if cc_opener is not None else _default_cc_opener()
        # Normalise to Path | None so downstream code (mcp_isolation) handles a
        # single concrete type regardless of whether a str or Path was passed.
        self._codex_config_home = (
            Path(codex_config_home) if codex_config_home is not None else None
        )
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

        slice-078: static M/P injection is computed here via the shared
        :func:`build_memory_injection` so the Memory Kernel has ONE owner (the
        cc and codex paths produce the same text for the same switches, C-4).
        The empty result (memory-off + no profile on disk) maps to ``None`` so
        the manager omits ``developerInstructions`` entirely. memory-on also
        attaches the trowel memory MCP via ``config.mcp_servers``; memory-off
        leaves it off (C-3: the whole read-path is closed, not just hidden).
        """

        if self._codex is None:
            raise HTTPException(status_code=503, detail="codex host unavailable")
        # slice-078 C-3: refuse up front when the user's codex config already
        # declares a same-named MCP server. ``--disable memories`` does NOT
        # unregister user MCP servers, so such a collision would let a
        # memory-off thread still expose a tool named ``trowel_note_search``,
        # making the off condition a lie (spec: "must detect and explicitly
        # fail, not claim off"). Checked for EVERY Codex session — the name is
        # specific enough that a real collision is always experiment-relevant.
        # HIGH-4 (codex review): check the project-level layers too, not just
        # the global config — codex deep-merges them.
        self._refuse_on_memory_mcp_collision(req.workdir)
        from trowel_py.codex_host import CodexSession, CodexSessionConfig
        from trowel_py.codex_host.session import build_default_trowel_memory_mcp
        from trowel_py.memory.injection import build_memory_injection
        from trowel_py.memory.paths import resolve_memory_root

        sid = uuid.uuid4().hex
        preset = req.permission_preset or "follow"
        approval_policy, sandbox = _CODEX_PERMISSION_PRESETS[preset]
        # Compatibility for callers from slice-072 that still send the two
        # native fields directly. New UI code sends permission_preset only.
        if req.permission_preset is None and (
            req.approval_policy is not None or req.sandbox is not None
        ):
            approval_policy = req.approval_policy
            sandbox = req.sandbox
        memory_root = resolve_memory_root()
        # Mirror CCHost._spawn's stance (cc_host/service.py): a memory-subsystem
        # failure must never block session creation. build_memory_injection can
        # raise on MemoryStore IO / profile parse / compress edge bugs; falling
        # back to "" maps to developer_instructions=None (no injection) so the
        # Codex thread still starts, matching how the CC path degrades.
        try:
            injection_text = build_memory_injection(
                date.today().isoformat(),
                memory_root,
                memory_enabled=req.memory_enabled,
                profile_enabled=req.profile_enabled,
            )
        except Exception:
            _log.warning(
                "memory injection failed; codex thread starts without it",
                exc_info=True,
            )
            injection_text = ""
        developer_instructions = injection_text or None
        injection_hash = _injection_fingerprint(injection_text)
        trowel_memory_mcp = (
            build_default_trowel_memory_mcp(
                trowel_session_id=sid,
                memory_root=str(memory_root),
            )
            if req.memory_enabled
            else None
        )
        # slice-078 C-7: persist the MCP server names trowel itself declared,
        # so experiments can tell a memory-on thread (has trowel_note_search)
        # from a memory-off one even after restart. External/user-configured
        # MCP servers are NOT here — their detection lives in mcp_isolation.
        declared_mcp_roster = (
            (trowel_memory_mcp.server_name,) if trowel_memory_mcp else ()
        )
        config = CodexSessionConfig(
            trowel_session_id=sid,
            workdir=req.workdir,
            model=req.model,
            effort=req.effort,
            approval_policy=approval_policy,
            sandbox=sandbox,
            initial_thread_id=req.resume_from,
            developer_instructions=developer_instructions,
            trowel_memory_mcp=trowel_memory_mcp,
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
            permission=None,
            memory_enabled=req.memory_enabled,
            profile_enabled=req.profile_enabled,
            capabilities=CODEX_CAPABILITIES,
            name=self._display_name(req.workdir),
            permission_preset=preset,
            injection_hash=injection_hash,
            declared_mcp_roster=declared_mcp_roster,
        )
        self._store.put(binding)
        self._active_id = sid
        return binding

    def _refuse_on_memory_mcp_collision(self, workdir: str) -> None:
        """slice-078 C-3: refuse session creation when the user's codex config
        already declares an MCP server with trowel's registered name.

        The name trowel registers (``trowel_note_search``) is specific enough
        that a collision is always experiment-relevant — either the user runs
        a conflicting memory server, or they happen to reuse the name, and in
        both cases a memory-off thread cannot be claimed clean. Production
        leaves ``codex_config_home`` unset so the real ``CODEX_HOME`` /
        ``~/.codex`` is read; tests inject a tmp dir. ``workdir`` is the
        session cwd — codex loads project-level config from there and from the
        git root, both of which are also checked (HIGH-4).
        """

        from trowel_py.codex_host.mcp_isolation import find_conflicting_mcp_server

        conflict = find_conflicting_mcp_server(
            codex_home=self._codex_config_home,
            workdir=workdir,
        )
        if conflict is not None:
            raise HTTPException(
                status_code=409,
                detail=(
                    f"a Codex MCP server named {conflict.server_name!r} is "
                    f"already declared in {conflict.config_path}; trowel "
                    f"cannot guarantee memory-off isolation. Rename or remove "
                    f"that entry and retry."
                ),
            )

    def _display_name(self, workdir: str) -> str:
        """Workdir basename + ``#N`` for duplicates, counted across the store."""

        basename = Path(workdir).name or str(workdir)
        same_workdir = sum(1 for b in self._store.list_all() if b.workdir == workdir)
        return basename if same_workdir == 0 else f"{basename} #{same_workdir + 1}"

    # ------------------------------------------------------------- read

    def get(self, session_id: str) -> SessionBinding | None:
        """Return the binding for ``session_id`` or ``None``."""

        return self._store.get(session_id)

    async def list_codex_models(self) -> list[dict[str, Any]]:
        """Return the shared app-server's complete visible model catalog."""

        if self._codex is None:
            raise HTTPException(status_code=503, detail="codex host unavailable")
        return await self._codex.list_models()

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

    def _live_status(self, binding: SessionBinding) -> tuple[bool, bool]:
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

    async def update_codex_settings(
        self,
        session_id: str,
        *,
        model: str | None,
        effort: str | None,
    ) -> dict[str, Any]:
        """Validate and stage one atomic Codex model/effort pair.

        Args:
            session_id: Trowel session whose next turn should use the pair.
            model: Requested native catalog id, or None to retain the current
                effective/configured model.
            effort: Requested native effort, or None to retain the current
                effort when supported.

        Returns:
            The staged pair plus whether effort was adjusted to the model's
            native default.

        Raises:
            HTTPException: For non-Codex sessions, missing live sessions,
                unknown models, or running/waiting sessions.
        """

        binding = self._require(session_id)
        if binding.runtime is not Runtime.CODEX:
            raise HTTPException(
                status_code=422, detail="model/effort PATCH is Codex-only"
            )
        if self._codex is None:
            raise HTTPException(status_code=503, detail="codex host unavailable")
        session = self._codex.get_session(session_id)
        if session is None:
            raise HTTPException(
                status_code=404, detail=f"codex session {session_id} not live"
            )
        catalog = await self._codex.list_models()
        current_native = getattr(session, "binding", None)
        default_row = next(
            (item for item in catalog if item.get("is_default") is True),
            catalog[0] if catalog else None,
        )
        current_model = (
            model
            or binding.model
            or getattr(current_native, "model", None)
            or session.config.model
            or (default_row.get("id") if default_row is not None else None)
        )
        row = next(
            (
                item
                for item in catalog
                if item.get("id") == current_model or item.get("model") == current_model
            ),
            None,
        )
        if row is None:
            raise HTTPException(
                status_code=422,
                detail=f"model {current_model!r} is not in the native catalog",
            )
        supported = [
            str(item["value"])
            for item in row.get("supported_efforts", [])
            if isinstance(item, dict) and isinstance(item.get("value"), str)
        ]
        requested_effort = (
            effort
            or binding.effort
            or getattr(current_native, "reasoning_effort", None)
            or session.config.effort
            or row.get("default_effort")
        )
        adjusted = requested_effort not in supported
        selected_effort = (
            str(row["default_effort"]) if adjusted else str(requested_effort)
        )
        if selected_effort not in supported:
            raise HTTPException(
                status_code=422,
                detail=f"model {current_model!r} has no usable default effort",
            )
        from trowel_py.codex_host import TurnConflictError

        try:
            session.queue_turn_settings(str(row["id"]), selected_effort)
        except TurnConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return {
            "model": str(row["id"]),
            "effort": selected_effort,
            "adjusted": adjusted,
        }

    def validate_resume(
        self,
        runtime: Runtime,
        native_session_id: str | None,
        *,
        memory_enabled: bool | None = None,
        profile_enabled: bool | None = None,
    ) -> None:
        """Forbid resuming a native id under a different runtime or M/P (C-2).

        A ``None`` native id is a fresh session — always allowed.

        Args:
            runtime: The runtime the caller wants to resume as.
            native_session_id: The native id being resumed.
            memory_enabled: slice-078 — when the caller supplied an explicit
                switch, it must equal the frozen value on the existing binding
                for the same native id (otherwise the experiment condition is
                being silently swapped). ``None`` means "caller did not pin"
                and skips the check (back-compat for pre-078 callers).
            profile_enabled: Same contract as ``memory_enabled`` for profile.

        Raises:
            CrossRuntimeResumeError: if ``native_session_id`` is already bound
                to a session whose runtime differs from ``runtime``.
            ConditionMismatchError: if the same native id is already bound
                under the same runtime but with a different M/P switch.
        """

        if native_session_id is None:
            return
        for binding in self._store.list_all():
            if binding.native_session_id != native_session_id:
                continue
            if binding.runtime is not runtime:
                raise CrossRuntimeResumeError(
                    f"native session {native_session_id!r} is bound to "
                    f"{binding.runtime.value}; cannot resume as "
                    f"{runtime.value} (C-2)"
                )
            # Same runtime + same native id: the M/P switches are frozen at the
            # original create. A mismatch means the caller is trying to resume
            # the same thread under a different experiment condition.
            if (
                memory_enabled is not None
                and binding.memory_enabled != memory_enabled
            ):
                raise ConditionMismatchError(
                    f"native session {native_session_id!r} is frozen with "
                    f"memory_enabled={binding.memory_enabled}; cannot resume "
                    f"as memory_enabled={memory_enabled} (C-2)"
                )
            if (
                profile_enabled is not None
                and binding.profile_enabled != profile_enabled
            ):
                raise ConditionMismatchError(
                    f"native session {native_session_id!r} is frozen with "
                    f"profile_enabled={binding.profile_enabled}; cannot "
                    f"resume as profile_enabled={profile_enabled} (C-2)"
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

    def answer_request(
        self, session_id: str, request_id: str, decision: str
    ) -> dict[str, Any]:
        """Answer one Codex pending request through its owning binding.

        Args:
            session_id: Trowel session from the route path.
            request_id: Connection-generation-scoped pending id.
            decision: Native choice key selected by the UI.

        Returns:
            The request's public terminal payload.

        Raises:
            HTTPException: For unknown/CC sessions, ownership errors, invalid
                decisions, or requests that are no longer pending.
        """

        binding = self._require(session_id)
        if binding.runtime is not Runtime.CODEX:
            raise HTTPException(
                status_code=422,
                detail="Codex pending-request answers cannot use the CC contract",
            )
        if self._codex is None:
            raise HTTPException(status_code=503, detail="codex host unavailable")
        try:
            request = self._codex.answer_request(session_id, request_id, decision)
        except PendingRequestNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except PendingRequestOwnershipError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except PendingRequestDecisionError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except PendingRequestConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return request.to_payload()

    def list_requests(self, session_id: str) -> list[dict[str, Any]]:
        """Return retained Codex request states for reconnect recovery."""

        binding = self._require(session_id)
        if binding.runtime is not Runtime.CODEX:
            return []
        if self._codex is None:
            raise HTTPException(status_code=503, detail="codex host unavailable")
        return [request.to_payload() for request in self._codex.list_requests(session_id)]

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

    async def stream(self, session_id: str, text: str) -> AsyncIterator[dict[str, Any]]:
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
            cc_adapter = self._cc_adapters.get(session_id)
            if cc_adapter is None:
                cc_adapter = CcEventAdapter(session_id)
                self._cc_adapters[session_id] = cc_adapter
            async for event in host.send(text):
                raw = dict(event) if isinstance(event, dict) else event.model_dump()
                yield cc_adapter.wrap(raw).model_dump(by_alias=True)
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
            await self._codex.send(
                session,
                text,
                before_turn_start=lambda attached: self._writeback_codex_native(
                    session_id, attached
                ),
            )
            # ``turn/start`` accepted: manager committed any pending model and
            # effort to the native binding. Persist that effective pair now;
            # the pre-turn callback above only makes the thread id durable.
            self._writeback_codex_native(session_id, session)
        except HTTPException:
            raise
        except Exception as exc:  # noqa: BLE001 — surface as 502, not 500
            _log.warning("codex turn start failed for %s: %s", session_id, exc)
            raise HTTPException(
                status_code=502, detail=f"codex turn failed: {exc}"
            ) from exc
        codex_adapter = self._codex_adapters.get(session_id)
        if codex_adapter is None:
            codex_adapter = CodexEventAdapter(session_id)
            self._codex_adapters[session_id] = codex_adapter
        async for event in session.events():
            envelope = codex_adapter.wrap(event)
            if envelope is None:
                # adapter dropped a duplicate (assistant_message) or a type with
                # no real producer (tool_progress); the per-session seq is only
                # advanced on emit, so no phantom gap is created.
                continue
            payload = envelope.model_dump(by_alias=True)
            yield payload
            if _is_terminal(payload):
                break

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
            cc_adapter = self._cc_adapters.get(session_id)
            if cc_adapter is None:
                cc_adapter = CcEventAdapter(session_id)
                self._cc_adapters[session_id] = cc_adapter
            return cc_adapter.error_event(detail).model_dump(by_alias=True)
        codex_adapter = self._codex_adapters.get(session_id)
        if codex_adapter is None:
            codex_adapter = CodexEventAdapter(session_id)
            self._codex_adapters[session_id] = codex_adapter
        return codex_adapter.error_event(detail).model_dump(by_alias=True)

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
            sandbox = getattr(thread_binding, "effective_sandbox", None)
            approval = getattr(thread_binding, "effective_approval", None)
            self._store.update_native(
                session_id,
                native_session_id=thread_binding.thread_id,
                model=thread_binding.model,
                effort=getattr(thread_binding, "reasoning_effort", None),
                permission=_permission_label(sandbox, approval),
                effective_permission_profile=getattr(
                    thread_binding, "permission_profile", None
                ),
                effective_sandbox=sandbox,
                effective_approval=approval,
                network_access=getattr(thread_binding, "network_access", None),
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
                1 for sid in self._codex.session_ids if self._store.get(sid) is not None
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


def _permission_label(sandbox: str | None, approval: str | None) -> str | None:
    """Build the compact compatibility label from separate effective facts."""

    labels = {
        "read-only": "Read only",
        "workspace-write": "Workspace write",
        "danger-full-access": "Full access",
    }
    if sandbox is None and approval is None:
        return None
    sandbox_label = labels.get(sandbox or "", sandbox or "Unknown sandbox")
    return f"{sandbox_label} · {approval or 'unknown approval'}"


def _injection_fingerprint(text: str) -> str:
    """Short sha of the static M/P injection text (slice-078 C-6).

    Empty text → empty string, so a memory-off + no-profile Codex session
    (no injection body at all) gets a falsy hash rather than ``sha256("")``.
    The binding stores only this fingerprint — never the injection body — so
    experiments can verify a resumed session kept its frozen condition without
    leaking private injection text into the persisted binding.

    12 hex chars = 48 bits; for a single user's experiment volume (well under
    10^6 frozen bindings) the birthday-collision probability is negligible
    (<< 10^-6). If this ever feeds a cross-user aggregate, raise the prefix.
    """

    if not text:
        return ""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]
