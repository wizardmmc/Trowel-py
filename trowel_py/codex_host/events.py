"""Trowel-side events emitted by the Codex host (slice-071).

This is the Codex translator's output boundary: every notification coming back
from ``codex app-server`` is reduced to one of these events before it leaves
:mod:`trowel_py.codex_host`. The frontend (slice-074) will later lift this into
a host-neutral ``AgentEvent v1``; for now the names and envelope fields are kept
deliberately aligned with that future contract so the migration is a rename,
not a redesign.

Field shapes trace back to the Rust protocol types in
``app-server-protocol/src/protocol/v2/`` (``turn.rs`` / ``thread.rs`` /
``item.rs``) at Codex 0.144.0 — see ``tests/codex_host/fixtures`` for the
matching real recordings. Nothing here is invented from documentation prose.

Design rules:
* Events are immutable (``frozen=True`` dataclasses) — once emitted they are
  handed to queues/reducers and must not be mutated in place.
* Every event carries the trowel ``session_id`` it belongs to and a per-session
  monotonically increasing ``seq`` (spec §2: routing id on every event).
* Native ids (``thread_id``/``turn_id``/``item_id``) are surfaced when the
  source notification provides them, so the UI can correlate deltas with the
  final completed item.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from types import MappingProxyType
from typing import Any, Mapping

# An immutable empty mapping reused as the default payload so events without a
# payload do not each allocate a fresh dict (and cannot be mutated by callers).
_EMPTY_PAYLOAD: Mapping[str, Any] = MappingProxyType({})


class CodexEventType(str, Enum):
    """Discriminator for every Codex event the translator can emit.

    Values mirror the baseline event names planned for ``AgentEvent v1``
    (slice-074) so a future adapter does not have to rename them.
    """

    # Lifecycle of a session / turn.
    SESSION_STARTED = "session_started"
    MODEL_CHANGED = "model_changed"
    TURN_STARTED = "turn_started"
    # User echo — emitted locally when ``turn/start`` is accepted (spec §2).
    USER = "user"
    # Streaming assistant output.
    ASSISTANT_DELTA = "assistant_delta"
    ASSISTANT_MESSAGE = "assistant_message"
    # Streaming reasoning (``item/reasoningText/delta`` + completed item).
    REASONING_DELTA = "reasoning_delta"
    # Command / tool lifecycle. ``tool_*`` covers ``commandExecution`` items
    # (file changes / MCP / dynamic tools stay behind capability flags until
    # their own slices record real fixtures).
    TOOL_STARTED = "tool_started"
    TOOL_PROGRESS = "tool_progress"
    TOOL_COMPLETED = "tool_completed"
    # Token usage, host status and terminal turn states.
    USAGE_UPDATED = "usage_updated"
    STATUS = "status"
    FINISHED = "finished"
    INTERRUPTED = "interrupted"
    ERROR = "error"
    # Host-level state changes (ready / degraded / host-exited).
    HOST_STATUS = "host_status"


class HostStatusKind(str, Enum):
    """The discrete host conditions surfaced through ``host_status`` events.

    These are the externally visible conditions the UI switches on, kept
    separate from :class:`~trowel_py.codex_host.manager.CodexHostManagerState`
    which is the manager's internal lifecycle state machine. A manager in
    ``degraded`` emits a ``HOST_EXITED`` status so a running turn observes a
    concrete terminal signal, not just a state flip.
    """

    READY = "ready"
    DEGRADED = "degraded"
    HOST_EXITED = "host_exited"
    # Reserved for slice-080 (proxy change → managed restart). Defined here so
    # the enum is stable across slices; slice-071 never emits it.
    RESTARTING = "restarting"


@dataclass(frozen=True)
class TranslatedItem:
    """One translated notification, before it is stamped with ids + seq.

    The translator is stateless and has no knowledge of which trowel session a
    thread is bound to — it only extracts the event shape from the native
    notification. The :class:`~trowel_py.codex_host.manager.CodexHostManager`
    routes by ``thread_id`` and asks the owning
    :class:`~trowel_py.codex_host.session.CodexSession` to stamp the item into
    a full :class:`CodexEvent` with ``session_id`` and ``seq``.

    Attributes:
        type: The event discriminator.
        thread_id: Native thread id when the source notification carried one
            (top-level ``params.threadId``). ``None`` for global notifications.
        turn_id: Native turn id when known (item/* and turn/* notifications).
        item_id: Native item id when the notification is about a specific item
            — stable across started/delta/completed so the UI can accumulate.
        payload: Structured, read-only fields specific to ``type``.
    """

    type: CodexEventType
    thread_id: str | None = None
    turn_id: str | None = None
    item_id: str | None = None
    payload: Mapping[str, Any] = _EMPTY_PAYLOAD


@dataclass(frozen=True)
class CodexEvent:
    """A fully addressed Codex event ready for a session event queue.

    Envelope fields line up with the planned ``AgentEvent v1`` schema
    (slice-074): ``session_id`` + ``seq`` for per-session ordering, ``runtime``
    fixed to ``"codex"`` here, and native ids for correlation.

    Attributes:
        session_id: The trowel session this event belongs to.
        seq: Per-session monotonically increasing sequence number. The session
            assigns it; cross-session seqs are never compared.
        type: The event discriminator (see :class:`CodexEventType`).
        thread_id: Native Codex thread id when known.
        turn_id: Native Codex turn id when known.
        item_id: Native Codex item id when known.
        payload: Read-only per-type fields.
    """

    session_id: str
    seq: int
    type: CodexEventType
    thread_id: str | None = None
    turn_id: str | None = None
    item_id: str | None = None
    payload: Mapping[str, Any] = field(default=_EMPTY_PAYLOAD)

    def as_dict(self) -> dict[str, Any]:
        """Return a JSON-serialisable view of the event for logging / SSE.

        ``payload`` is unwrapped to a plain dict so the result is directly
        JSON-dumpable; the event itself stays immutable.
        """

        return {
            "schema": "codex-event-v1",
            "session_id": self.session_id,
            "runtime": "codex",
            "seq": self.seq,
            "type": self.type.value,
            "thread_id": self.thread_id,
            "turn_id": self.turn_id,
            "item_id": self.item_id,
            "payload": dict(self.payload),
        }


def immutable_payload(**fields: Any) -> Mapping[str, Any]:
    """Build a read-only payload mapping from keyword fields.

    Centralising this keeps every payload an immutable
    :class:`types.MappingProxyType`, so a buggy consumer cannot mutate an event
    in place after it has been queued.
    """

    return MappingProxyType(dict(fields))


def host_status_item(
    status: HostStatusKind,
    *,
    thread_id: str | None = None,
    reason: str | None = None,
    exit_code: int | None = None,
) -> TranslatedItem:
    """Build a HOST_STATUS translated item.

    Used by the manager when it flips ready/degraded or fans out a host-exited
    signal to running turns — these are not translations of a single
    notification, they are synthesised from transport state.
    """

    payload_fields: dict[str, Any] = {"status": status.value}
    if reason is not None:
        payload_fields["reason"] = reason
    if exit_code is not None:
        payload_fields["exit_code"] = exit_code
    return TranslatedItem(
        type=CodexEventType.HOST_STATUS,
        thread_id=thread_id,
        payload=immutable_payload(**payload_fields),
    )
