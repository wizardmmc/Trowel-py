"""Reduce raw Codex app-server notifications into :class:`TranslatedItem`.

Stateless and side-effect free: hand it ``(method, params)`` and it returns the
zero-or-more translated items the UI should see. Routing, session stamping and
diagnostic bookkeeping live in :mod:`trowel_py.codex_host.manager`.

Every field read below comes from one of three grounded sources, never from a
guess or from documentation prose:

1. The real 2026-07-18 spike recordings under ``tests/codex_host/fixtures``.
2. The Rust protocol types in ``app-server-protocol/src/protocol/v2/``
   (``turn.rs`` / ``thread.rs`` / ``item.rs``) at Codex 0.144.0.
3. The 0.144.0 schema baseline kept next to the fixtures.

When a notification we do map is missing a required field, that is protocol
drift, not a tolerable runtime blip — we raise
:class:`~trowel_py.codex_host.errors.ProtocolViolationError` and let the
manager turn it into a structured ERROR event (spec: never fake success).
"""

from __future__ import annotations

import re
from typing import Any, Callable, Mapping

from trowel_py.codex_host.errors import ProtocolViolationError
from trowel_py.codex_host.events import (
    CodexEventType,
    TranslatedItem,
    immutable_payload,
)

# Methods we intentionally do not translate in slice-071. Keeping this set
# explicit (rather than silently returning ``[]`` for anything unknown) makes
# it obvious which capabilities are deferred to later slices and forces a
# conscious decision when a new method shows up in a recording.
_IGNORED_METHODS: frozenset[str] = frozenset(
    {
        # thread/start response already produced session_started with the full
        # facts (model/provider/sandbox). The follow-up notification has no
        # top-level threadId and would only duplicate the signal.
        "thread/started",
        # turn/started notification is an echo of the turn/start response —
        # ``CodexSession.record_turn_started`` already emitted TURN_STARTED
        # with the turn id, so translating the notification would duplicate it.
        "turn/started",
        # Capability-gated areas with their own slices — no real fixture yet.
        "account/rateLimits/updated",  # slice-077 (rate-limit view)
        "mcpServer/startupStatus/updated",  # slice-078 (memory MCP wiring)
        "serverRequest/resolved",  # approval bus echo (slice-075)
        "thread/turns/list",  # history pagination (slice-079)
        "thread/items/list",
    }
)

# Turn terminal states (``turn/completed.turn.status``), from
# ``app-server-protocol/src/protocol/v2/turn.rs::TurnStatus``.
_TURN_COMPLETED = "completed"
_TURN_INTERRUPTED = "interrupted"
_TURN_FAILED = "failed"
_TURN_IN_PROGRESS = "inProgress"

# ``item.type`` tag values, from ``v2/item.rs::ThreadItem``. Only the kinds
# slice-071 translates are listed; the rest fall through to "ignored".
_ITEM_COMMAND = "commandExecution"
_ITEM_AGENT_MSG = "agentMessage"
_ITEM_REASONING = "reasoning"
_ITEM_FILE_CHANGE = "fileChange"

# ``FileUpdateChange.kind.type`` tags (``v2/item.rs::PatchChangeKind``).
_FC_ADD = "add"
_FC_DELETE = "delete"
_FC_UPDATE = "update"

# ``commandExecution.status``, from ``v2/item.rs::CommandExecutionStatus``.
_CMD_IN_PROGRESS = "inProgress"
_CMD_FAILED = "failed"
_CMD_DECLINED = "declined"

# ``v2/item.rs::CommandAction`` tags at Codex 0.144.0. This is deliberately a
# closed set: silently accepting a future tag would let the UI assign semantics
# we have not verified against the native source.
_COMMAND_ACTION_FIELDS: Mapping[str, tuple[str, ...]] = {
    "read": ("command", "name", "path"),
    "listFiles": ("command", "path"),
    "search": ("command", "query", "path"),
    "unknown": ("command",),
}


def _require(params: Mapping[str, Any], key: str, method: str) -> Any:
    """Return ``params[key]`` or raise a protocol-violation if absent.

    A mapped notification missing a documented field is drift; we refuse to
    synthesise a fallback (spec: no compatible-branch guessing).
    """

    if key not in params:
        raise ProtocolViolationError(
            f"notification {method!r} missing required field {key!r}",
            payload=dict(params),
        )
    return params[key]


def _as_str(value: Any) -> str:
    """Coerce a non-None notification field to ``str`` for ids and text."""

    return value if isinstance(value, str) else str(value)


def _command_actions(item: Mapping[str, Any], method: str) -> tuple[dict[str, Any], ...]:
    """Validate and copy Codex 0.144.0 ``commandActions`` for the UI bridge."""

    raw_actions = _require(item, "commandActions", method)
    if not isinstance(raw_actions, list):
        raise ProtocolViolationError(
            f"notification {method!r} commandActions is not an array",
            payload=dict(item),
        )
    actions: list[dict[str, Any]] = []
    for raw in raw_actions:
        if not isinstance(raw, Mapping):
            raise ProtocolViolationError(
                f"notification {method!r} commandActions entry is not an object",
                payload=dict(item),
            )
        action_type = raw.get("type")
        if not isinstance(action_type, str):
            raise ProtocolViolationError(
                f"notification {method!r} commandActions has non-string type",
                payload=dict(item),
            )
        fields = _COMMAND_ACTION_FIELDS.get(action_type)
        if fields is None:
            raise ProtocolViolationError(
                f"notification {method!r} commandActions has unexpected type {action_type!r}",
                payload=dict(item),
            )
        action: dict[str, Any] = {"type": action_type}
        action.update({field: raw.get(field) for field in fields})
        actions.append(action)
    return tuple(actions)


# Regex for a unified-diff hunk header ``@@ -o[,ol] +n[,nl] @@``. The count
# groups are optional: Codex emits ``@@ -1 +1 @@`` for single-line hunks.
_HUNK_HEADER = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")


def _parse_unified_diff(patch: str) -> tuple[dict[str, Any], ...]:
    """Parse a unified diff string into hunk dicts.

    A Codex ``FileUpdateChange.diff`` for an ``update`` change is a standard
    unified diff. ``add`` / ``delete`` changes carry full file content in
    ``diff`` and never reach this parser.

    Each dict mirrors the FE ``DiffHunk`` shape
    (``{oldStart, oldLines, newStart, newLines, lines}``); ``lines`` keeps its
    leading `` `` / ``+`` / ``-`` marker. A missing count defaults to 1
    (standard unified-diff elision). Lines outside any hunk (file headers,
    ``\\ No newline at end of file``) are skipped.

    Args:
        patch: The ``diff`` string from a Codex ``FileUpdateChange``.

    Returns:
        Tuple of hunk dicts in document order; empty when there is no
        ``@@`` header.
    """

    hunks: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    lines_buf: list[str] = []
    for line in patch.splitlines():
        match = _HUNK_HEADER.match(line)
        if match:
            if current is not None:
                current["lines"] = tuple(lines_buf)
                hunks.append(current)
            current = {
                "oldStart": int(match.group(1)),
                "oldLines": int(match.group(2)) if match.group(2) is not None else 1,
                "newStart": int(match.group(3)),
                "newLines": int(match.group(4)) if match.group(4) is not None else 1,
            }
            lines_buf = []
            continue
        if current is None:
            continue
        if line.startswith((" ", "+", "-")):
            lines_buf.append(line)
    if current is not None:
        current["lines"] = tuple(lines_buf)
        hunks.append(current)
    return tuple(hunks)


def _full_file_hunk(text: str, marker: str) -> tuple[dict[str, Any], ...]:
    """Build a single all-add or all-remove hunk from full file content.

    Codex ``FileUpdateChange.diff`` for ``add`` / ``delete`` carries the full
    file content (not a unified diff). Convert it to one hunk where every line
    carries the ``+`` (add) or ``-`` (delete) marker so the existing diff
    renderer paints it as a green-only / red-only block with a correct stat.

    Args:
        text: The full file content (``FileUpdateChange.diff`` for add/delete).
        marker: ``"+"`` for add, ``"-"`` for delete.

    Returns:
        A one-tuple of the hunk dict, or empty when the file has no lines.
    """

    lines = text.splitlines()
    if not lines:
        return ()
    marked = tuple(f"{marker}{line}" for line in lines)
    count = len(lines)
    if marker == "+":
        return (
            {
                "oldStart": 0,
                "oldLines": 0,
                "newStart": 1,
                "newLines": count,
                "lines": marked,
            },
        )
    return (
        {
            "oldStart": 1,
            "oldLines": count,
            "newStart": 0,
            "newLines": 0,
            "lines": marked,
        },
    )


def _file_change_write_diff(kind_type: Any, diff: Any) -> dict[str, Any]:
    """Build a normalized ``write_diff`` dict for one file change.

    The shape mirrors the FE ``WriteDiff`` (``{type, hunks}``) with Codex's
    ``delete`` added (CC only emits ``create`` / ``update``):

    * ``add``    → ``{type: "create", hunks: all-added hunk from full content}``.
    * ``delete`` → ``{type: "delete", hunks: all-removed hunk from full content}``.
    * ``update`` → ``{type: "update", hunks: parsed from unified_diff}``.

    Add/delete carry full file content in ``diff`` (not a unified diff);
    converting it to an all-add/all-remove hunk lets the FE reuse the existing
    diff renderer + stat pill without a separate "full content preview" path.

    Args:
        kind_type: The ``PatchChangeKind.type`` tag.
        diff: The raw ``diff`` string from ``FileUpdateChange``.

    Returns:
        The normalized ``write_diff`` dict.
    """

    text = str(diff or "")
    if kind_type == _FC_ADD:
        return {"type": "create", "hunks": _full_file_hunk(text, "+")}
    if kind_type == _FC_DELETE:
        return {"type": "delete", "hunks": _full_file_hunk(text, "-")}
    if kind_type == _FC_UPDATE:
        return {"type": "update", "hunks": _parse_unified_diff(text)}
    # Unreachable in production: ``_file_change_to_change`` raises on an
    # unknown kind tag before calling this. Defence-in-depth — raise rather
    # than synthesise a ``type: "unknown"`` dict the FE ``WriteDiff`` type
    # cannot represent, so a future caller that bypasses the guard fails
    # loudly instead of leaking an untyped value over the wire.
    raise ProtocolViolationError(
        f"fileChange write_diff: unexpected kind type {kind_type!r}",
        payload={"kind_type": kind_type},
    )


def _file_change_to_change(change: Mapping[str, Any], method: str) -> dict[str, Any]:
    """Translate one Codex ``FileUpdateChange`` into normalized props.

    Codex's ``update{movePath}`` collapses to ``change_kind="rename"`` when a
    move target is set, else ``"modify"``. ``move_path`` is preserved
    separately so the UI can show the rename target without re-reading ``kind``.

    Args:
        change: One element of ``item.changes`` (a ``FileUpdateChange``).
        method: The source notification method (for error messages).

    Returns:
        ``{path, change_kind, move_path, write_diff}``.

    Raises:
        ProtocolViolationError: If ``kind`` is missing/not an object, or its
            ``type`` is not a known ``PatchChangeKind`` tag.
    """

    kind = change.get("kind")
    if not isinstance(kind, Mapping):
        raise ProtocolViolationError(
            f"notification {method!r} fileChange change.kind is not an object",
            payload=dict(change),
        )
    kind_type = kind.get("type")
    move_path_raw = kind.get("movePath")
    move_path = str(move_path_raw) if move_path_raw else None
    if kind_type == _FC_ADD:
        change_kind = "add"
    elif kind_type == _FC_DELETE:
        change_kind = "delete"
    elif kind_type == _FC_UPDATE:
        change_kind = "rename" if move_path else "modify"
    else:
        raise ProtocolViolationError(
            f"notification {method!r} fileChange change.kind.type has "
            f"unexpected value {kind_type!r}",
            payload=dict(change),
        )
    raw_path = change.get("path")
    return {
        "path": _as_str(raw_path) if raw_path is not None else "",
        "change_kind": change_kind,
        "move_path": move_path,
        "write_diff": _file_change_write_diff(kind_type, change.get("diff")),
    }


class CodexTranslator:
    """Map one native notification to zero or more translated items.

    The class holds no state — a single shared instance is fine. Methods are
    organised by source method so the dispatch table reads as a roster of
    what the translator understands.
    """

    def __init__(self) -> None:
        """Build the method → handler dispatch table."""

        self._dispatch: dict[str, Callable[[Mapping[str, Any]], list[TranslatedItem]]] = {
            "turn/completed": self._on_turn_completed,
            "item/agentMessage/delta": self._on_agent_message_delta,
            "item/reasoning/textDelta": self._on_reasoning_delta,
            "item/reasoning/summaryTextDelta": self._on_reasoning_delta,
            "item/started": self._on_item_started,
            "item/completed": self._on_item_completed,
            "thread/tokenUsage/updated": self._on_token_usage,
            "thread/status/changed": self._on_thread_status,
            "error": self._on_error,
        }

    def translate(self, method: str, params: Mapping[str, Any]) -> list[TranslatedItem]:
        """Translate one notification.

        Args:
            method: The JSON-RPC ``method`` string from the notification.
            params: The notification ``params`` object.

        Returns:
            The translated items (possibly empty). Empty means "known but not
            translated this slice" or "unknown method"; the caller decides
            whether to record an orphan/unknown diagnostic.

        Raises:
            ProtocolViolationError: If a mapped notification is missing a
                documented required field (protocol drift).
        """

        handler = self._dispatch.get(method)
        if handler is not None:
            return handler(params)
        return []

    @property
    def ignored_methods(self) -> frozenset[str]:
        """Methods intentionally not translated this slice (for diagnostics)."""

        return _IGNORED_METHODS

    # --------------------------------------------------------------- turn

    def _on_turn_completed(self, params: Mapping[str, Any]) -> list[TranslatedItem]:
        """``turn/completed`` → FINISHED / INTERRUPTED / ERROR per native status.

        ``turn/completed`` is the method name; the real terminal state is
        ``turn.status`` which may legitimately be ``interrupted`` or ``failed``
        — mapping it to FINISHED unconditionally would hide interrupts.
        """

        thread_id = _require(params, "threadId", "turn/completed")
        turn = _require(params, "turn", "turn/completed")
        if not isinstance(turn, Mapping):
            raise ProtocolViolationError(
                "turn/completed.turn is not an object",
                payload=dict(params),
            )
        turn_id = turn.get("id")
        status = turn.get("status")
        # status is the documented discriminator (v2/turn.rs::TurnStatus); a
        # missing or inProgress status here means the protocol shape changed.
        if status not in (_TURN_COMPLETED, _TURN_INTERRUPTED, _TURN_FAILED):
            raise ProtocolViolationError(
                f"turn/completed.turn.status has unexpected value {status!r}",
                payload=dict(params),
            )
        payload = immutable_payload(
            turn_id=_as_str(turn_id) if turn_id is not None else None,
            status=_as_str(status),
            error=turn.get("error"),
            duration_ms=turn.get("durationMs"),
            completed_at=turn.get("completedAt"),
        )
        if status == _TURN_COMPLETED:
            item_type = CodexEventType.FINISHED
        elif status == _TURN_INTERRUPTED:
            item_type = CodexEventType.INTERRUPTED
        else:
            item_type = CodexEventType.ERROR
        return [
            TranslatedItem(
                type=item_type,
                thread_id=_as_str(thread_id),
                turn_id=_as_str(turn_id) if turn_id is not None else None,
                payload=payload,
            )
        ]

    # ------------------------------------------------------------- streaming

    def _on_agent_message_delta(
        self, params: Mapping[str, Any]
    ) -> list[TranslatedItem]:
        """``item/agentMessage/delta`` → one ASSISTANT_DELTA (item-id stable)."""

        return [
            TranslatedItem(
                type=CodexEventType.ASSISTANT_DELTA,
                thread_id=_as_str(_require(params, "threadId", "item/agentMessage/delta")),
                turn_id=_as_str(_require(params, "turnId", "item/agentMessage/delta")),
                item_id=_as_str(_require(params, "itemId", "item/agentMessage/delta")),
                payload=immutable_payload(delta=_as_str(_require(params, "delta", "item/agentMessage/delta"))),
            )
        ]

    def _on_reasoning_delta(self, params: Mapping[str, Any]) -> list[TranslatedItem]:
        """``item/reasoningText/delta`` (and summary variant) → REASONING_DELTA.

        Both streaming reasoning channels carry ``itemId`` + ``delta``; the
        summary variant adds ``summaryIndex`` which we surface in the payload
        so the UI can keep summary and raw text segments apart.
        """

        item_id = _as_str(_require(params, "itemId", "reasoning delta"))
        payload_fields: dict[str, Any] = {
            "delta": _as_str(_require(params, "delta", "reasoning delta")),
        }
        if "summaryIndex" in params:
            payload_fields["summary_index"] = params["summaryIndex"]
        if "contentIndex" in params:
            payload_fields["content_index"] = params["contentIndex"]
        return [
            TranslatedItem(
                type=CodexEventType.REASONING_DELTA,
                thread_id=_as_str(_require(params, "threadId", "reasoning delta")),
                turn_id=_as_str(_require(params, "turnId", "reasoning delta")),
                item_id=item_id,
                payload=immutable_payload(**payload_fields),
            )
        ]

    # ------------------------------------------------------------------ item

    def _on_item_started(self, params: Mapping[str, Any]) -> list[TranslatedItem]:
        """``item/started`` → TOOL_STARTED for commands and file changes.

        agentMessage / reasoning items stream via their own ``*delta`` methods,
        so their ``started`` notification carries no extra information the UI
        needs; MCP calls are deferred to later slices.
        """

        item = _require(params, "item", "item/started")
        if not isinstance(item, Mapping):
            raise ProtocolViolationError(
                "item/started.item is not an object", payload=dict(params)
            )
        item_type = item.get("type")
        if item_type == _ITEM_COMMAND:
            return [self._command_started_item(params, item)]
        if item_type == _ITEM_FILE_CHANGE:
            return [self._file_change_started_item(params, item)]
        return []

    def _on_item_completed(self, params: Mapping[str, Any]) -> list[TranslatedItem]:
        """``item/completed`` → terminal item event by ``item.type``."""

        item = _require(params, "item", "item/completed")
        if not isinstance(item, Mapping):
            raise ProtocolViolationError(
                "item/completed.item is not an object", payload=dict(params)
            )
        item_type = item.get("type")
        if item_type == _ITEM_COMMAND:
            return [self._command_completed_item(params, item)]
        if item_type == _ITEM_AGENT_MSG:
            return [self._agent_message_item(params, item)]
        if item_type == _ITEM_FILE_CHANGE:
            return [self._file_change_completed_item(params, item)]
        # reasoning completed: deltas already streamed; final summary is a
        # future slice. Other item kinds are capability-gated.
        return []

    def _command_started_item(
        self, params: Mapping[str, Any], item: Mapping[str, Any]
    ) -> TranslatedItem:
        """Build a TOOL_STARTED for a ``commandExecution`` item."""

        return TranslatedItem(
            type=CodexEventType.TOOL_STARTED,
            thread_id=_as_str(_require(params, "threadId", "item/started")),
            turn_id=_as_str(_require(params, "turnId", "item/started")),
            item_id=_as_str(item.get("id")),
            payload=immutable_payload(
                kind=_ITEM_COMMAND,
                command=item.get("command"),
                cwd=item.get("cwd"),
                source=item.get("source"),
                command_actions=_command_actions(item, "item/started"),
                started_at=params.get("startedAtMs"),
            ),
        )

    def _command_completed_item(
        self, params: Mapping[str, Any], item: Mapping[str, Any]
    ) -> TranslatedItem:
        """Build a TOOL_COMPLETED for a ``commandExecution`` item.

        ``status`` is preserved in the payload so the UI can show
        ``failed`` / ``declined`` distinctly from ``completed``.
        """

        return TranslatedItem(
            type=CodexEventType.TOOL_COMPLETED,
            thread_id=_as_str(_require(params, "threadId", "item/completed")),
            turn_id=_as_str(_require(params, "turnId", "item/completed")),
            item_id=_as_str(item.get("id")),
            payload=immutable_payload(
                kind=_ITEM_COMMAND,
                command=item.get("command"),
                cwd=item.get("cwd"),
                source=item.get("source"),
                command_actions=_command_actions(item, "item/completed"),
                status=item.get("status"),
                exit_code=item.get("exitCode"),
                output=item.get("aggregatedOutput"),
                duration_ms=item.get("durationMs"),
                completed_at=params.get("completedAtMs"),
            ),
        )

    def _file_change_started_item(
        self, params: Mapping[str, Any], item: Mapping[str, Any]
    ) -> TranslatedItem:
        """Build a TOOL_STARTED for a ``fileChange`` item (apply_patch started).

        The item carries ``changes: [FileUpdateChange]`` with status
        ``inProgress``; the normalized changes let the UI paint a per-file
        diff placeholder while the patch is in flight.
        """

        return TranslatedItem(
            type=CodexEventType.TOOL_STARTED,
            thread_id=_as_str(_require(params, "threadId", "item/started")),
            turn_id=_as_str(_require(params, "turnId", "item/started")),
            item_id=_as_str(item.get("id")),
            payload=immutable_payload(
                kind=_ITEM_FILE_CHANGE,
                changes=tuple(
                    _file_change_to_change(c, "item/started")
                    for c in _require(item, "changes", "item/started")
                ),
                status=item.get("status"),
                started_at=params.get("startedAtMs"),
            ),
        )

    def _file_change_completed_item(
        self, params: Mapping[str, Any], item: Mapping[str, Any]
    ) -> TranslatedItem:
        """Build a TOOL_COMPLETED for a ``fileChange`` item.

        ``status`` (``completed`` / ``failed`` / ``declined``) is preserved so
        the UI does not paint a declined or failed patch as a successful write
        (spec: declined/failed file item must not show as success).
        """

        return TranslatedItem(
            type=CodexEventType.TOOL_COMPLETED,
            thread_id=_as_str(_require(params, "threadId", "item/completed")),
            turn_id=_as_str(_require(params, "turnId", "item/completed")),
            item_id=_as_str(item.get("id")),
            payload=immutable_payload(
                kind=_ITEM_FILE_CHANGE,
                changes=tuple(
                    _file_change_to_change(c, "item/completed")
                    for c in _require(item, "changes", "item/completed")
                ),
                status=item.get("status"),
                completed_at=params.get("completedAtMs"),
            ),
        )

    def _agent_message_item(
        self, params: Mapping[str, Any], item: Mapping[str, Any]
    ) -> TranslatedItem:
        """Build an ASSISTANT_MESSAGE with the full final text + phase."""

        return TranslatedItem(
            type=CodexEventType.ASSISTANT_MESSAGE,
            thread_id=_as_str(_require(params, "threadId", "item/completed")),
            turn_id=_as_str(_require(params, "turnId", "item/completed")),
            item_id=_as_str(item.get("id")),
            payload=immutable_payload(
                text=item.get("text"),
                phase=item.get("phase"),
            ),
        )

    # ------------------------------------------------------------- thread

    def _on_token_usage(self, params: Mapping[str, Any]) -> list[TranslatedItem]:
        """``thread/tokenUsage/updated`` → USAGE_UPDATED (threadId + turnId)."""

        usage = _require(params, "tokenUsage", "thread/tokenUsage/updated")
        if not isinstance(usage, Mapping):
            raise ProtocolViolationError(
                "thread/tokenUsage/updated.tokenUsage is not an object",
                payload=dict(params),
            )
        return [
            TranslatedItem(
                type=CodexEventType.USAGE_UPDATED,
                thread_id=_as_str(_require(params, "threadId", "thread/tokenUsage/updated")),
                turn_id=_as_str(params.get("turnId")) if params.get("turnId") is not None else None,
                payload=immutable_payload(
                    total=usage.get("total"),
                    last=usage.get("last"),
                    model_context_window=usage.get("modelContextWindow"),
                ),
            )
        ]

    def _on_thread_status(self, params: Mapping[str, Any]) -> list[TranslatedItem]:
        """``thread/status/changed`` → STATUS (active/idle + active flags).

        ``activeFlags`` (``WaitingOnApproval`` / ``WaitingOnUserInput``) tells
        the UI why the thread is paused, even before approval UI ships.
        """

        status = _require(params, "status", "thread/status/changed")
        if not isinstance(status, Mapping):
            raise ProtocolViolationError(
                "thread/status/changed.status is not an object",
                payload=dict(params),
            )
        return [
            TranslatedItem(
                type=CodexEventType.STATUS,
                thread_id=_as_str(_require(params, "threadId", "thread/status/changed")),
                payload=immutable_payload(
                    status=status.get("type"),
                    active_flags=tuple(status.get("activeFlags") or ()),
                ),
            )
        ]

    def _on_error(self, params: Mapping[str, Any]) -> list[TranslatedItem]:
        """``error`` notification → ERROR (surfaces ``will_retry`` to the UI).

        ``willRetry=true`` means the app-server will keep the turn alive; the
        UI still gets an ERROR so it can show the transient failure, but the
        payload flag lets it avoid marking the turn terminal.
        """

        error = _require(params, "error", "error")
        if not isinstance(error, Mapping):
            raise ProtocolViolationError(
                "error.error is not an object", payload=dict(params)
            )
        return [
            TranslatedItem(
                type=CodexEventType.ERROR,
                thread_id=_as_str(_require(params, "threadId", "error")),
                turn_id=_as_str(params.get("turnId")) if params.get("turnId") is not None else None,
                payload=immutable_payload(
                    kind="native_error",
                    error_type=error.get("type"),
                    message=error.get("message"),
                    will_retry=bool(params.get("willRetry", False)),
                ),
            )
        ]
