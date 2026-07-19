"""Pydantic request/response models for the ``/api/agent`` routes (slice-072).

Kept separate from the runtime :class:`~trowel_py.agent_host.binding.SessionBinding`
dataclass: these are the wire shapes only, validated at the API boundary
(spec: fail fast with clear messages; never trust external input). The Hub
works in terms of :class:`SessionBinding`; the routes translate between the
two.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

RuntimeWire = Literal["claude_code", "codex"]
PermissionPreset = Literal[
    "follow", "read-only", "workspace-write", "danger-full-access"
]


class CreateAgentSessionRequest(BaseModel):
    """Body for POST ``/api/agent/sessions``.

    ``runtime`` chooses the native host and is frozen at create (spec C-1).
    The permission-shaped fields are runtime-specific (CC takes
    ``permission_mode``; Codex takes ``approval_policy`` + ``sandbox``); the
    Hub passes only the field the chosen runtime understands, so cross-runtime
    leakage in a request body is ignored rather than misrouted.

    Attributes:
        runtime: ``claude_code`` or ``codex`` — frozen at create.
        workdir: absolute working directory the session runs in.
        resume_from: optional native session id to resume (CC ``cc_session_id``
            or Codex ``thread_id``). Validated against runtime ownership (C-2).
        model / effort: optional overrides; the host's effective value wins.
        permission_mode: CC ``--permission-mode`` (defaults to bypass).
        approval_policy: Codex ``approvalPolicy`` (defaults to ``never``).
        sandbox: Codex ``sandbox`` mode (defaults to ``read-only``).
        permission_preset: Host-neutral Codex permission intent. The backend
            maps it to native sandbox/approval fields.
        memory_enabled / profile_enabled: frozen A/B switches (default True).
    """

    runtime: RuntimeWire
    workdir: str = Field(min_length=1)
    resume_from: str | None = None
    model: str | None = None
    effort: str | None = None
    permission_mode: str | None = None
    approval_policy: str | None = None
    sandbox: str | None = None
    permission_preset: PermissionPreset | None = None
    memory_enabled: bool = Field(default=True, strict=True)
    profile_enabled: bool = Field(default=True, strict=True)


class PatchAgentSessionRequest(BaseModel):
    """Body for PATCH ``/api/agent/sessions/{id}``.

    slice-072 only needs to reject a runtime change (spec C-1 → 422). Other
    fields are accepted by the model but the Hub ignores them this slice
    (model/effort changes follow each host's own contract, later slices).
    """

    runtime: str | None = None
    model: str | None = None
    effort: str | None = None


class SendMessageBody(BaseModel):
    """Body for POST ``/api/agent/sessions/{id}/messages``."""

    text: str = Field(min_length=1)


class AnswerAgentRequest(BaseModel):
    """Body for answering one Codex app-server server request.

    The decision remains an open string at the HTTP boundary because native
    choices are request-specific. The manager validates it against the exact
    recorded ``availableDecisions`` for that pending request.
    """

    decision: str = Field(min_length=1)
