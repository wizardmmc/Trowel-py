"""Codex → AgentEvent v1 adapter (slice-074).

Maps a :class:`~trowel_py.codex_host.events.CodexEvent` onto the unified
envelope with **TrowelEvent-aligned type names** (people-confirmed 2026-07-19).
Codex's own translator keeps reflecting the app-server protocol untouched
(spec §2: "Codex 使用独立 translator") — the name + payload remapping lives
here, at the envelope boundary.

Why remap names at all: the frontend has ONE reducer (the CC
:func:`~trowel_py`... ``ccReducer``) switching on ``type``. Codex events must
arrive speaking the same type vocabulary, or the reducer would need a parallel
switch. Unifying to the existing TrowelEvent names lets a single reducer render
both runtimes (spec C-7: gradual rename — we adapt Codex onto CC's mature
contract, not the other way round).

Two Codex events have no CC equivalent and keep their own type as named
extensions (spec §1): ``usage_updated`` (per-turn token accounting) and
``host_status`` (manager ready/degraded/host_exited).

``assistant_message`` is dropped (returns ``None``): the streaming
``assistant_delta`` events already accumulated the full text, so the final
agentMessage item would duplicate it. The hub skips ``None``.

The adapter owns a per-session contiguous ``seq`` counter, incremented only
when an event is actually emitted (not when one is dropped — e.g.
``assistant_message``). This keeps the envelope seq hole-free even though
Codex's native seq advances for every notification: a dropped event must not
create a phantom gap that the frontend would flag as ``needsReplay``.
"""

from __future__ import annotations

from typing import Any, Callable, Literal, Mapping

from trowel_py.codex_host.events import CodexEvent, CodexEventType
from trowel_py.schemas.agent_host import AgentEvent

#: Discriminator value the adapter stamps on every Codex envelope.
_CODEX_RUNTIME: Literal["codex"] = "codex"

#: Codex ``commandExecution`` items render as a tool named "command" (the CC
#: reducer builds a ToolItem from tool_name + input; Codex commands are shell
#: invocations, closest CC analogue is the Bash tool's command rendering).
_COMMAND_TOOL_NAME = "command"

#: Codex ``fileChange`` item kind tag (translator payload ``kind`` value).
_FILE_CHANGE_KIND = "fileChange"

#: Codex apply_patch file changes render as a tool named "apply_patch". The CC
#: reducer builds a ToolItem from tool_name + input; the closest CC analogue
#: for a file edit is the Write/Edit tool family, which the FE renders via
#: ``write_diff`` attached on the matching tool_result.
_APPLY_PATCH_TOOL_NAME = "apply_patch"


class CodexEventAdapter:
    """Map one :class:`CodexEvent` to an :class:`AgentEvent` (or drop it).

    One instance per Codex session (the hub holds them in a dict, mirroring
    :class:`~trowel_py.agent_host.cc_adapter.CcEventAdapter`). The per-session
    ``seq`` counter is the only state; it spans turns within a session.
    """

    def __init__(self, session_id: str) -> None:
        """Bind the adapter to one trowel session id.

        Args:
            session_id: the trowel session id stamped on every envelope.
        """

        self._session_id = session_id
        self._seq = 0
        self._dispatch: dict[
            CodexEventType, Callable[[CodexEvent], AgentEvent | None]
        ] = {
            CodexEventType.SESSION_STARTED: self._session_started,
            CodexEventType.MODEL_CHANGED: self._model_changed,
            CodexEventType.TURN_STARTED: self._turn_started,
            CodexEventType.USER: self._user,
            CodexEventType.ASSISTANT_DELTA: self._assistant_delta,
            CodexEventType.ASSISTANT_MESSAGE: self._drop,
            CodexEventType.REASONING_DELTA: self._reasoning_delta,
            CodexEventType.TOOL_STARTED: self._tool_started,
            CodexEventType.TOOL_PROGRESS: self._drop,
            CodexEventType.TOOL_COMPLETED: self._tool_completed,
            CodexEventType.APPROVAL_REQUEST: self._approval_request,
            CodexEventType.USAGE_UPDATED: self._usage_updated,
            CodexEventType.RATE_LIMIT_UPDATED: self._rate_limit_updated,
            CodexEventType.STATUS: self._status,
            CodexEventType.FINISHED: self._finished,
            CodexEventType.INTERRUPTED: self._interrupted,
            CodexEventType.ERROR: self._error,
            CodexEventType.HOST_STATUS: self._host_status,
            CodexEventType.COMPACTION: self._compaction,
        }

    @property
    def session_id(self) -> str:
        """The session this adapter stamps onto every envelope."""

        return self._session_id

    def wrap(self, event: CodexEvent) -> AgentEvent | None:
        """Map one Codex event, or return ``None`` to drop it.

        Args:
            event: a fully-addressed :class:`CodexEvent` from the manager.

        Returns:
            The v1 envelope, or ``None`` for events the UI should not see
            (``assistant_message`` duplicates streamed deltas; ``tool_progress``
            has no real Codex producer and would carry a null elapsed-time).
        """

        handler = self._dispatch.get(event.type)
        if handler is None:
            # Unknown Codex type — drop rather than guess. The manager already
            # records orphan diagnostics; an envelope with a fabricated type
            # would violate spec C-1.
            return None
        return handler(event)

    def error_event(self, detail: Any) -> AgentEvent:
        """Build a terminal error envelope from the SAME per-session seq space.

        Mirrors :meth:`CcEventAdapter.error_event` — route-level failures must
        not collide with prior events' seq or the frontend drops them as dups.
        """

        self._seq += 1
        return AgentEvent(
            session_id=self._session_id,
            runtime=_CODEX_RUNTIME,
            seq=self._seq,
            type="error",
            payload={"subclass": "host_error", "errors": [str(detail)]},
        )

    # --------------------------------------------------------------- lifecycle

    def _session_started(self, e: CodexEvent) -> AgentEvent:
        """session_started → CC SessionStartedEvent shape (model/cwd/cc_session_id/tools).

        The native thread id becomes ``cc_session_id`` (CC's field name for the
        native session id) so the reducer's session_started case works unchanged.
        Effective permission facts also ride this first live event so the
        frontend shell can update immediately after lazy ``thread/start``;
        the shared reducer safely ignores those extra fields.
        """

        return self._envelope(
            e,
            type_="session_started",
            payload={
                "model": e.payload.get("model"),
                "cwd": e.payload.get("cwd"),
                "cc_session_id": e.thread_id,
                "tools": [],
                "permission_profile": e.payload.get("permission_profile"),
                "effective_sandbox": e.payload.get("effective_sandbox"),
                "effective_approval": e.payload.get("effective_approval"),
                "network_access": e.payload.get("network_access"),
            },
        )

    def _turn_started(self, e: CodexEvent) -> AgentEvent:
        """turn_started → CC turn_start; Codex has no checkpoint concept (revertible False)."""

        return self._envelope(e, type_="turn_start", payload={"revertible": False})

    def _model_changed(self, e: CodexEvent) -> AgentEvent:
        """Accepted next-turn settings reuse the shared model_changed event."""

        return self._envelope(
            e,
            type_="model_changed",
            payload={
                "model": e.payload.get("model"),
                "effort": e.payload.get("effort"),
            },
        )

    def _user(self, e: CodexEvent) -> AgentEvent:
        """user echo passes through (text only)."""

        return self._envelope(e, type_="user", payload={"text": e.payload.get("text")})

    # ------------------------------------------------------------- streaming

    def _assistant_delta(self, e: CodexEvent) -> AgentEvent:
        """assistant_delta → text; payload.delta → payload.text."""

        return self._envelope(e, type_="text", payload={"text": e.payload.get("delta")})

    def _reasoning_delta(self, e: CodexEvent) -> AgentEvent:
        """reasoning_delta → thinking; payload.delta → payload.text."""

        return self._envelope(
            e, type_="thinking", payload={"text": e.payload.get("delta")}
        )

    def _drop(self, e: CodexEvent) -> AgentEvent | None:
        """assistant_message duplicates streamed deltas — drop (return None)."""

        return None

    # ------------------------------------------------------------------ tool

    def _tool_started(self, e: CodexEvent) -> AgentEvent:
        """item/started → tool_call, by item ``kind``.

        ``commandExecution`` → tool named 'command'; ``fileChange`` → tool
        named 'apply_patch' with the target file paths.
        """

        if e.payload.get("kind") == _FILE_CHANGE_KIND:
            return self._file_change_started_envelope(e)
        return self._envelope(
            e,
            type_="tool_call",
            payload={
                "tool_use_id": e.item_id,
                "tool_name": _COMMAND_TOOL_NAME,
                "input": {
                    "command": e.payload.get("command"),
                    "cwd": e.payload.get("cwd"),
                    "source": e.payload.get("source"),
                    "command_actions": [
                        dict(action)
                        for action in (e.payload.get("command_actions") or ())
                    ],
                },
                "started_at_ms": e.payload.get("started_at"),
            },
        )

    def _file_change_started_envelope(self, e: CodexEvent) -> AgentEvent:
        """fileChange started → tool_call named 'apply_patch' (target paths)."""

        changes = [dict(change) for change in (e.payload.get("changes") or ())]
        return self._envelope(
            e,
            type_="tool_call",
            payload={
                "tool_use_id": e.item_id,
                "tool_name": _APPLY_PATCH_TOOL_NAME,
                "input": {
                    "paths": [c["path"] for c in changes],
                    "change_kinds": [c["change_kind"] for c in changes],
                },
                "started_at_ms": e.payload.get("started_at"),
            },
        )

    def _tool_completed(self, e: CodexEvent) -> AgentEvent:
        """item/completed → tool_result, by item ``kind``.

        ``commandExecution`` → content/exit_code/duration; ``fileChange`` →
        per-file ``write_diff`` + ``change_kind`` + native ``status`` so a
        declined or failed patch is not painted as a successful write.
        """

        if e.payload.get("kind") == _FILE_CHANGE_KIND:
            return self._file_change_completed_envelope(e)
        return self._envelope(
            e,
            type_="tool_result",
            payload={
                "tool_use_id": e.item_id,
                "content": e.payload.get("output"),
                "exit_code": e.payload.get("exit_code"),
                "duration_ms": e.payload.get("duration_ms"),
                "cwd": e.payload.get("cwd"),
                "command": e.payload.get("command"),
                "status": e.payload.get("status"),
            },
        )

    def _file_change_completed_envelope(self, e: CodexEvent) -> AgentEvent:
        """fileChange completed → tool_result with write_diff + change_kind.

        Codex records one fileChange item per file (verified in the 2026-07-19
        probe), so the first change's ``write_diff`` is attached at the payload
        top level for the existing CC diff renderer. The full ``changes`` list
        is also carried so a future batched multi-file item remains
        introspectable without an adapter change.
        """

        changes = [dict(change) for change in (e.payload.get("changes") or ())]
        first = changes[0] if changes else {}
        return self._envelope(
            e,
            type_="tool_result",
            payload={
                "tool_use_id": e.item_id,
                "tool_name": _APPLY_PATCH_TOOL_NAME,
                "content": None,
                "change_kind": first.get("change_kind"),
                "path": first.get("path"),
                "move_path": first.get("move_path"),
                "write_diff": first.get("write_diff"),
                "changes": changes,
                "status": e.payload.get("status"),
                "completed_at_ms": e.payload.get("completed_at"),
            },
        )

    # ------------------------------------------------------------- extensions

    def _usage_updated(self, e: CodexEvent) -> AgentEvent:
        """usage_updated (extension) — passthrough token accounting."""

        return self._passthrough(e, type_="usage_updated")

    def _compaction(self, e: CodexEvent) -> AgentEvent:
        """compaction (extension) — a contextCompaction item, slice-088.

        Only the ``completed`` phase reaches here: the translator routes
        ``item/started(contextCompaction)`` to a no-op (083 / codex review A5 —
        ``thread/compact/start`` returning {} does not close a boundary). The
        payload stamps ``phase="completed"`` so a context observer can advance
        its generation without guessing the item phase.
        """

        payload = dict(e.payload)
        # hardcoded: only item/completed reaches here (started is a no-op in
        # the translator, 083 A5), so phase is always "completed".
        payload["phase"] = "completed"
        return self._envelope(e, type_="compaction", payload=payload)

    def _rate_limit_updated(self, e: CodexEvent) -> AgentEvent:
        """rate_limit_updated (extension) — passthrough account-level snapshot.

        Source: ``account/rateLimits/updated`` (slice-077). The manager fans
        this out to every active Codex session because it is account-scoped;
        the adapter just passes the snapshot payload through so the UI can
        render the rate-limit banner.
        """

        return self._passthrough(e, type_="rate_limit_updated")

    def _host_status(self, e: CodexEvent) -> AgentEvent:
        """host_status (extension) — passthrough manager lifecycle."""

        return self._passthrough(e, type_="host_status")

    def _approval_request(self, e: CodexEvent) -> AgentEvent:
        """Pass through the manager-generated approval lifecycle payload."""

        return self._passthrough(e, type_="approval_request")

    # ------------------------------------------------------------- terminal

    def _status(self, e: CodexEvent) -> AgentEvent:
        """thread/status/changed → status; stage ← status.type, active_flags carried."""

        return self._envelope(
            e,
            type_="status",
            payload={
                "stage": e.payload.get("status"),
                "active_flags": list(e.payload.get("active_flags") or ()),
            },
        )

    def _finished(self, e: CodexEvent) -> AgentEvent:
        """finished → CC finished; Codex has no cost/num_turns → nulls."""

        return self._envelope(
            e,
            type_="finished",
            payload={
                "usage": None,
                "total_cost_usd": None,
                "num_turns": None,
                # Codex-native extras the reducer ignores but diagnostics keep.
                "duration_ms": e.payload.get("duration_ms"),
            },
        )

    def _interrupted(self, e: CodexEvent) -> AgentEvent:
        """interrupted → CC interrupted."""

        return self._envelope(e, type_="interrupted", payload={})

    def _error(self, e: CodexEvent) -> AgentEvent:
        """Split native vs turn-failed errors (gpt5.6 Critical 2).

        Codex has TWO sources of ``CodexEventType.ERROR``:

        * ``error`` notification (translator ``_on_error``) — payload carries
          ``kind="native_error"``. CodexSession treats this as NON-terminal and
          keeps waiting for ``turn/completed``; the ``will_retry`` flag hints
          whether the app-server will retry. Map to CC ``retrying`` (a
          non-terminal transient failure) so the hub keeps streaming and the
          reducer shows a heads-up, NOT a terminal error.
        * ``turn/completed`` with ``status="failed"`` (translator
          ``_on_turn_completed``) — payload has NO ``kind``. The turn really
          ended; map to CC ``error`` (terminal).

        Before this split, the adapter dropped ``kind`` and the hub keyed
        terminal-ness off ``will_retry``, which left CodexSession RUNNING after
        the hub stopped (future sends rejected) AND marked retryable turns as
        terminal in the UI.
        """

        if e.payload.get("kind") == "native_error":
            return self._envelope(
                e,
                type_="retrying",
                payload={
                    "attempt": 1,
                    "max_retries": None,
                    "error_status": None,
                    "error": e.payload.get("message"),
                    "retry_delay_ms": None,
                },
            )
        # turn/completed status=failed → terminal error
        err = e.payload.get("error")
        message = (
            err
            if isinstance(err, str)
            else err.get("message")
            if isinstance(err, dict)
            else None
        )
        return self._envelope(
            e,
            type_="error",
            payload={
                "subclass": "turn_failed",
                "errors": _message_to_errors(message),
                "api_error_status": None,
            },
        )

    # ------------------------------------------------------------- internals

    def _passthrough(self, e: CodexEvent, *, type_: str) -> AgentEvent:
        """Build an envelope whose payload is the Codex payload verbatim."""

        return self._envelope(e, type_=type_, payload=dict(e.payload))

    def _envelope(
        self, e: CodexEvent, *, type_: str, payload: Mapping[str, Any]
    ) -> AgentEvent:
        """Stamp the unified envelope with the next contiguous per-session seq.

        ``seq`` is the adapter's own counter (NOT Codex's native seq) so dropped
        events do not punch holes in the sequence the frontend dedupes on. The
        native ids (``turn_id`` / ``item_id``) still pass through for delta↔
        completed correlation.
        """

        self._seq += 1
        return AgentEvent(
            session_id=self._session_id,
            runtime=_CODEX_RUNTIME,
            seq=self._seq,
            type=type_,
            turn_id=_opt_str(e.turn_id),
            item_id=_opt_str(e.item_id),
            payload=dict(payload),
        )


def _opt_str(value: Any) -> str | None:
    """Return ``value`` when it is a string, else None."""

    return value if isinstance(value, str) else None


def _message_to_errors(message: Any) -> list[str]:
    """Wrap a Codex error message into CC's ``errors: [str]`` shape."""

    if isinstance(message, str) and message:
        return [message]
    return []
