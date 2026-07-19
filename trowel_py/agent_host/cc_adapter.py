"""CC → AgentEvent v1 adapter (slice-074).

This is the "outer adapter" spec §2 requires: CC's existing translator/history
keeps producing :mod:`trowel_py.schemas.cc_host` TrowelEvent models unchanged,
and this module wraps each ``model_dump()`` into the unified envelope. Nothing
about CC's translation moves — the adapter is purely the envelope + seq layer.

State:

* ``seq`` is per-session monotonic (spec §1: starts at 1, never compared across
  sessions). CC had no per-session seq before slice-074; the adapter owns the
  counter so :class:`~trowel_py.agent_host.hub.SessionHub` can hold one
  ``CcEventAdapter`` per CC session.

Field mapping:

* ``type`` passes through (CC's type names are already the v1 vocabulary).
* ``payload`` is a shallow copy of the event minus ``type`` — the frontend
  reducer reads the same field names it always has, just under ``payload.*``.
* ``turn_id`` is taken from :class:`~trowel_py.schemas.cc_host.TurnStartEvent`
  (the only CC event that carries one); null on every other event. CC history
  replay has no turn_start, so replayed events are null here too.
* ``item_id`` is taken from ``tool_use_id`` (tool_call / tool_progress /
  tool_result / elicit_request / subagent_progress), which is the stable id
  across a tool's started→progress→completed lifecycle (spec §2).
"""

from __future__ import annotations

from typing import Any

from trowel_py.schemas.agent_host import AgentEvent

#: CC event fields whose value is a stable item id, lifted onto the envelope's
#: ``item_id`` for delta↔completed correlation. Kept in payload too so the
#: reducer (which reads payload) keeps working unchanged.
_ITEM_ID_FIELDS: tuple[str, ...] = ("tool_use_id",)


def _coerce_optional_str(value: Any) -> str | None:
    """Return ``value`` when it is a string, else None (envelope ids are str)."""

    return value if isinstance(value, str) else None


def _shallow_copy_minus_type(event: dict[str, Any]) -> dict[str, Any]:
    """Copy the event dict, dropping the ``type`` key (it lives on the envelope).

    A shallow copy is enough — we never mutate nested values, and the copy
    protects the caller's original dict from later envelope-side writes.
    """

    return {k: v for k, v in event.items() if k != "type"}


class CcEventAdapter:
    """Per-session wrapper turning CC TrowelEvent dicts into AgentEvent v1.

    One instance per CC session; :meth:`wrap` is called for every event the
    hub streams off ``CCHost.send()``. The seq counter is the only state.
    """

    def __init__(self, session_id: str) -> None:
        """Bind the adapter to one trowel session id.

        Args:
            session_id: the trowel session id stamped on every envelope.
        """

        self._session_id = session_id
        self._seq = 0

    @property
    def session_id(self) -> str:
        """The session this adapter stamps onto every envelope."""

        return self._session_id

    def wrap(self, event: dict[str, Any]) -> AgentEvent:
        """Wrap one CC TrowelEvent dict into an :class:`AgentEvent` envelope.

        Args:
            event: a ``TrowelEvent.model_dump()`` dict (must carry ``type``).

        Returns:
            The v1 envelope with the next per-session seq stamped on it.

        Raises:
            KeyError: if ``event`` has no ``type`` key (a malformed input —
                the adapter refuses to guess a type).
        """

        self._seq += 1
        event_type = event["type"]
        return AgentEvent(
            session_id=self._session_id,
            runtime="claude_code",
            seq=self._seq,
            type=event_type,
            turn_id=_coerce_optional_str(event.get("turn_id")),
            item_id=_item_id_from_event(event),
            payload=_shallow_copy_minus_type(event),
        )

    def error_event(self, detail: Any) -> AgentEvent:
        """Build a terminal error envelope from the SAME per-session seq space.

        Route-level failures (unknown session, host down, turn-start failure)
        must share the session's seq allocator so the error frame is never a
        duplicate of an earlier event's seq — otherwise the frontend's dup
        guard would drop it and a clean SSE close would mark the turn done
        (gpt5.6 Critical 1).
        """

        self._seq += 1
        return AgentEvent(
            session_id=self._session_id,
            runtime="claude_code",
            seq=self._seq,
            type="error",
            payload={"subclass": "host_error", "errors": [str(detail)]},
        )


def _item_id_from_event(event: dict[str, Any]) -> str | None:
    """Lift the stable item id (tool_use_id) onto the envelope when present."""

    for field in _ITEM_ID_FIELDS:
        value = event.get(field)
        if isinstance(value, str):
            return value
    return None
