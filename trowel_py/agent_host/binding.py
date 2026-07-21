"""Session binding schema for the host-neutral Session Hub (slice-072).

A :class:`SessionBinding` is the frozen public shape of one trowel agent
session — the answer to "which runtime owns this session, and which native
session id is it bound to?". ``runtime`` is frozen at create (spec C-1); only
``native_session_id`` and the effective facts learned from the native host
get written back later, and even then immutably (a fresh dataclass replaces
the old one — never in-place mutation).
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from enum import Enum


class Runtime(str, Enum):
    """The two native agent runtimes trowel drives (spec §1).

    Values are the wire strings the API and the frontend switch on; kept as a
    ``str`` enum so ``Runtime.CLAUDE_CODE == "claude_code"`` holds and JSON
    serialisation is the bare string (no ``.value`` dance at the boundary).
    """

    CLAUDE_CODE = "claude_code"
    CODEX = "codex"


@dataclass(frozen=True)
class SessionBinding:
    """The frozen public shape of one agent session.

    Attributes:
        session_id: trowel session id (the routing key).
        runtime: native host that owns this session; frozen at create.
        native_session_id: ``cc_session_id`` / Codex ``thread_id``, or ``None``
            for a fresh session until the native host reports it (spec: a bare
            id with no runtime is never legal).
        workdir: absolute working directory the session runs in.
        model: effective model once the host reports one; else ``None``.
        effort: reasoning-effort override or ``None``.
        permission: runtime-specific effective policy string, or ``None``.
        memory_enabled: frozen memory A/B switch.
        profile_enabled: frozen profile A/B switch.
        capabilities: runtime-declared capability tags (``tools`` / ``approval``
            / …) — the UI keys off this list, never off ``runtime == …`` (C-6).
        name: multi-session display name (workdir basename + ``#N``).
        connected: ``True`` once the native host has a live process/thread.
        running: ``True`` while a turn is mid-stream.
        created_at / updated_at: ISO timestamps (second precision).
        injection_hash: slice-078 — short sha of the static M/P injection text
            (empty on CC, and on a Codex memory-off + no-profile session whose
            injection is empty). Lets experiments verify a resumed session
            kept its frozen condition without storing the injection body in
            the binding (C-6).
        declared_mcp_roster: slice-078 — the MCP server names trowel itself
            attached on this thread (``("trowel_note_search",)`` on a Codex
            memory-on session; empty otherwise). This is the *declared*
            roster, not the *effective* one — external MCP servers the user
            configured are not visible here (their detection lives in
            ``codex_host.mcp_isolation``); a future slice may add a live-roster
            field populated from ``mcpServerStatus/list`` (C-7).
        self_enabled: slice-085 — whether the Self section (the Trowel
            "你是谁/能做什么" shell-on-top of cc/codex's default system prompt)
            is injected this call. Default True; False drops the entire Self
            section for an A/B baseline. Frozen at create so a resumed session
            keeps its condition, like memory_enabled/profile_enabled.
    """

    session_id: str
    runtime: Runtime
    native_session_id: str | None
    workdir: str
    model: str | None
    effort: str | None
    permission: str | None
    memory_enabled: bool
    profile_enabled: bool
    capabilities: tuple[str, ...]
    name: str
    connected: bool = False
    running: bool = False
    created_at: str = ""
    updated_at: str = ""
    permission_preset: str | None = None
    effective_permission_profile: str | None = None
    effective_sandbox: str | None = None
    effective_approval: str | None = None
    network_access: bool | None = None
    injection_hash: str = ""
    declared_mcp_roster: tuple[str, ...] = ()
    self_enabled: bool = True

    def to_dict(self) -> dict[str, object]:
        """JSON-serialisable view (runtime + capabilities unwrapped)."""

        return {
            "session_id": self.session_id,
            "runtime": self.runtime.value,
            "native_session_id": self.native_session_id,
            "workdir": self.workdir,
            "model": self.model,
            "effort": self.effort,
            "permission": self.permission,
            "memory_enabled": self.memory_enabled,
            "profile_enabled": self.profile_enabled,
            "capabilities": list(self.capabilities),
            "name": self.name,
            "connected": self.connected,
            "running": self.running,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "permission_preset": self.permission_preset,
            "effective_permission_profile": self.effective_permission_profile,
            "effective_sandbox": self.effective_sandbox,
            "effective_approval": self.effective_approval,
            "network_access": self.network_access,
            "injection_hash": self.injection_hash,
            "declared_mcp_roster": list(self.declared_mcp_roster),
            "self_enabled": self.self_enabled,
        }


def make_binding(
    *,
    session_id: str,
    runtime: Runtime,
    native_session_id: str | None,
    workdir: str,
    model: str | None,
    effort: str | None,
    permission: str | None,
    memory_enabled: bool,
    profile_enabled: bool,
    capabilities: Iterable[str],
    name: str,
    connected: bool = False,
    running: bool = False,
    permission_preset: str | None = None,
    effective_permission_profile: str | None = None,
    effective_sandbox: str | None = None,
    effective_approval: str | None = None,
    network_access: bool | None = None,
    injection_hash: str = "",
    declared_mcp_roster: Iterable[str] = (),
    self_enabled: bool = True,
) -> SessionBinding:
    """Construct a :class:`SessionBinding`, stamping both timestamps at now.

    Centralising timestamping here means callers never hand-build a binding
    with a stale/empty ``created_at``.
    """

    now = datetime.now().isoformat(timespec="seconds")
    return SessionBinding(
        session_id=session_id,
        runtime=runtime,
        native_session_id=native_session_id,
        workdir=workdir,
        model=model,
        effort=effort,
        permission=permission,
        memory_enabled=memory_enabled,
        profile_enabled=profile_enabled,
        capabilities=tuple(capabilities),
        name=name,
        connected=connected,
        running=running,
        permission_preset=permission_preset,
        effective_permission_profile=effective_permission_profile,
        effective_sandbox=effective_sandbox,
        effective_approval=effective_approval,
        network_access=network_access,
        injection_hash=injection_hash,
        declared_mcp_roster=tuple(declared_mcp_roster),
        self_enabled=self_enabled,
        created_at=now,
        updated_at=now,
    )


def binding_from_dict(data: dict[str, object]) -> SessionBinding:
    """Reconstruct a binding from its persisted dict shape.

    Tolerant on optional fields so an older/partial file still loads; strict
    on ``runtime`` (a bad value is drift, not a default).

    Raises:
        KeyError: if a required field (``session_id`` / ``runtime`` /
            ``workdir`` / ``name``) is missing.
        ValueError: if ``runtime`` is not a known :class:`Runtime` value.
    """

    capabilities = data.get("capabilities", ())
    return SessionBinding(
        session_id=str(data["session_id"]),
        runtime=Runtime(str(data["runtime"])),
        native_session_id=(
            str(data["native_session_id"]) if data.get("native_session_id") else None
        ),
        workdir=str(data["workdir"]),
        model=str(data["model"]) if data.get("model") is not None else None,
        effort=str(data["effort"]) if data.get("effort") is not None else None,
        permission=(
            str(data["permission"]) if data.get("permission") is not None else None
        ),
        memory_enabled=bool(data.get("memory_enabled", True)),
        profile_enabled=bool(data.get("profile_enabled", True)),
        capabilities=tuple(str(c) for c in capabilities)  # type: ignore[arg-type]
        if isinstance(capabilities, (list, tuple))
        else (),
        name=str(data["name"]),
        connected=bool(data.get("connected", False)),
        running=bool(data.get("running", False)),
        created_at=str(data.get("created_at", "")),
        updated_at=str(data.get("updated_at", "")),
        permission_preset=(
            str(data["permission_preset"])
            if data.get("permission_preset") is not None
            else None
        ),
        effective_permission_profile=(
            str(data["effective_permission_profile"])
            if data.get("effective_permission_profile") is not None
            else None
        ),
        effective_sandbox=(
            str(data["effective_sandbox"])
            if data.get("effective_sandbox") is not None
            else None
        ),
        effective_approval=(
            str(data["effective_approval"])
            if data.get("effective_approval") is not None
            else None
        ),
        network_access=(
            bool(data["network_access"])
            if data.get("network_access") is not None
            else None
        ),
        injection_hash=str(data.get("injection_hash", "")),
        declared_mcp_roster=tuple(
            str(s) for s in (data.get("declared_mcp_roster") or ())
        ),
        self_enabled=bool(data.get("self_enabled", True)),
    )
