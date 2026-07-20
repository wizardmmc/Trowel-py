"""AgentEvent v1 — the single wire contract both runtimes emit (slice-074).

After slice-074 the frontend consumes ONE event shape on every path (live SSE
+ history replay): this envelope. CC and Codex no longer have separate wire
contracts; each runtime's adapter (``agent_host/cc_adapter.py`` +
``agent_host/codex_adapter.py``) wraps its native trowel translation into this
envelope before it leaves :mod:`trowel_py.agent_host`.

Design decisions (people-confirmed 2026-07-19):

* **Unified to the TrowelEvent name set.** The CC contract
  (:mod:`trowel_py.schemas.cc_host`) already is trowel's mature "tcc general
  event" layer, so Codex events map onto those names rather than the spec's
  aspirational v1 base list. The spec's base list is treated as a *semantic*
  coverage checklist, not a literal rename mandate (spec C-7: gradual rename,
  no big-bang).
* **Two Codex-driven extensions.** Codex surfaces token usage per turn
  (``usage_updated``) and host-level ready/degraded/host_exited transitions
  (``host_status``); neither has a CC equivalent, so they join the vocabulary
  as named extensions instead of being squeezed into a fake generic field
  (spec: "host 特有事件使用明确 extension type/payload").
* **Envelope, not a discriminated union.** Per-type field validation already
  happens upstream (CC's typed TrowelEvent Pydantic models; Codex's
  translator). The envelope is the wire wrapper: one model, a ``type``
  discriminator validated against the vocabulary, and a free-form ``payload``.
  This keeps the boundary flat and avoids re-modelling 20+ event shapes.

``seq`` is per-session monotonic (spec §1: starts at 1, never compared across
sessions). CC gains a per-session counter in the adapter; Codex already stamps
``seq`` on :class:`~trowel_py.codex_host.events.CodexEvent`.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from trowel_py.schemas.cc_host import EVENT_TYPES as _CC_EVENT_TYPES

#: The schema discriminator stamped on every envelope (spec §1 wire shape).
AGENT_EVENT_SCHEMA: Literal["agent-event-v1"] = "agent-event-v1"

#: Codex events that have no CC equivalent surface as named extensions rather
#: than being forced through a generic field (spec §1: "host 特有事件使用明确
#: extension type/payload"). CC's compact_boundary stays its own name (Codex
#: has no compaction event in slice-071 fixtures).
_CODEX_EXTENSION_TYPES: frozenset[str] = frozenset(
    {
        # Per-turn token accounting (thread/tokenUsage/updated).
        "usage_updated",
        # Manager lifecycle (ready / degraded / host_exited) — synthesised from
        # transport state, not a single notification.
        "host_status",
        # Connection-scoped Codex approval lifecycle (slice-075).
        "approval_request",
        # Account-level rate-limit snapshot (slice-077; account/rateLimits/updated).
        "rate_limit_updated",
    }
)

#: Every accepted ``type`` discriminator. The TrowelEvent (CC) contract plus the
#: Codex extensions above. Importing CC's set keeps the two in lockstep — a new
#: CC event type is automatically a valid envelope type without a second edit.
AGENT_EVENT_TYPES: frozenset[str] = _CC_EVENT_TYPES | _CODEX_EXTENSION_TYPES

#: Runtime tag carried on every envelope (spec §1). Matches ``RuntimeWire`` in
#: :mod:`trowel_py.agent_host.schemas`; re-declared here so the event schema
#: has no runtime-layer import (the wire contract is leaf-level).
AgentRuntime = Literal["claude_code", "codex"]


class AgentEvent(BaseModel):
    """One host-neutral event on the live stream or history replay path.

    Attributes:
        schema_version: Wire key ``schema``, always ``"agent-event-v1"``. Names
            the contract so a future v2 is detectable at the boundary.
        session_id: The trowel session this event belongs to (routing key).
        runtime: ``claude_code`` or ``codex`` — frozen at session create.
        seq: Per-session monotonically increasing sequence number (≥ 1). Cross-
            session seqs are never compared; the frontend uses it to drop dups
            and flag gaps (spec §3).
        type: Discriminator aligned to the TrowelEvent vocabulary + the Codex
            extensions. Validated against :data:`AGENT_EVENT_TYPES`.
        turn_id: Native or trowel turn id when known; null otherwise.
        item_id: Native item id when the event is about a specific item (stable
            across started/delta/completed so the UI can accumulate).
        payload: Per-type fields (read as-is by the frontend reducer).
    """

    model_config = ConfigDict(populate_by_name=True)

    schema_version: Literal["agent-event-v1"] = Field(
        default=AGENT_EVENT_SCHEMA, alias="schema"
    )
    session_id: str = Field(min_length=1)
    runtime: AgentRuntime
    seq: int = Field(ge=1)
    type: str
    turn_id: str | None = None
    item_id: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)

    @field_validator("type")
    @classmethod
    def _type_in_vocabulary(cls, value: str) -> str:
        """Reject unmapped types — an unknown type is an adapter bug, not a
        passthrough (spec C-1: fail fast at the boundary)."""

        if value not in AGENT_EVENT_TYPES:
            raise ValueError(
                f"unknown agent event type {value!r}; not in AGENT_EVENT_TYPES"
            )
        return value
