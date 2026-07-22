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
        "mcpServer/startupStatus/updated",  # slice-078 (memory MCP wiring)
        "serverRequest/resolved",  # approval bus echo (slice-075)
        "thread/turns/list",  # history pagination (slice-079)
        "thread/items/list",
        # slice-077 capability=false (handlers ready below — _on_plan_updated /
        # _on_warning): turn/plan/updated needs todo-mcp (codex-vscode/todo-mcp);
        # warning family needs a misconfigured MCP / deprecated API. Remove the
        # entry to activate once a real fixture is recorded.
        "turn/plan/updated",
        "warning",
        "guardianWarning",
        "configWarning",
        "deprecationNotice",
    }
)

# Account-level notifications carry no top-level ``threadId`` by design — they
# describe the OpenAI account itself, not any one thread. The manager fans these
# out to every active Codex session instead of orphaning them as ``no_thread_id``
# (slice-077: rate-limit is account-scoped). Adding a method here is a conscious
# decision that the protocol defines it as account-level (source:
# ``account.rs:518`` for ``account/rateLimits/updated``).
_ACCOUNT_LEVEL_METHODS: frozenset[str] = frozenset(
    {
        "account/rateLimits/updated",  # slice-077 (account.rs:518)
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
_ITEM_MCP_TOOL = "mcpToolCall"
# slice-077 capability=false item types — handlers ready, not routed until
# activated. Source: item.rs:359 (subAgentActivity) / item.rs:388 (contextCompaction).
_ITEM_SUBAGENT = "subAgentActivity"
_ITEM_COMPACT = "contextCompaction"

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


def _mcp_tool_name(server: Any, tool: Any) -> str:
    """Build ``<server>.<tool>`` for the reducer's ToolItem key.

    Tolerates a missing half (an item with no ``server`` or no ``tool`` field)
    so a future schema variant does not break translation; the combined name
    is what the CC reducer keys ``ToolItem`` on, and an empty-handed MCP call
    falls back to a generic ``mcp`` so it still renders as a tool.
    """

    parts = [str(p) for p in (server, tool) if isinstance(p, str) and p]
    return ".".join(parts) if parts else "mcp"


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
            "account/rateLimits/updated": self._on_rate_limits,
            # slice-077 capability=false — registered but gated by
            # _IGNORED_METHODS. Activate by removing the ignored entry.
            "turn/plan/updated": self._on_plan_updated,
            "warning": self._on_warning,
            "guardianWarning": self._on_warning,
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

    @property
    def account_level_methods(self) -> frozenset[str]:
        """Account-level methods (no threadId) — manager fans out to all sessions.

        Unlike ``ignored_methods`` (dropped silently) these are translated, but
        the result is broadcast to every active Codex session because the source
        notification has no thread binding (slice-077).
        """

        return _ACCOUNT_LEVEL_METHODS

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
        """``item/started`` → TOOL_STARTED for commands, file changes, MCP calls.

        agentMessage / reasoning items stream via their own ``*delta`` methods,
        so their ``started`` notification carries no extra information the UI
        needs.
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
        if item_type == _ITEM_MCP_TOOL:
            return [self._mcp_tool_started_item(params, item)]
        if item_type == _ITEM_SUBAGENT:
            # capability=false (slice-077): _subagent_item is ready below;
            # route once a real fixture is recorded.
            return []
        if item_type == _ITEM_COMPACT:
            # slice-088 (083 / codex review A5): item/started(contextCompaction)
            # is intentionally a no-op — only item/completed closes a context
            # generation. thread/compact/start returning {} is not a boundary.
            return []
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
        if item_type == _ITEM_MCP_TOOL:
            return [self._mcp_tool_completed_item(params, item)]
        if item_type == _ITEM_SUBAGENT:
            return []  # capability=false (slice-077): _subagent_item ready, not routed
        if item_type == _ITEM_COMPACT:
            # slice-088: activate the completed boundary (083 / codex review A5).
            # Only item/completed(contextCompaction) closes a context generation.
            return [self._compaction_item(params, item)]
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

    def _mcp_tool_started_item(
        self, params: Mapping[str, Any], item: Mapping[str, Any]
    ) -> TranslatedItem:
        """Build a TOOL_STARTED for an ``mcpToolCall`` item (slice-078).

        Carries ``server``/``tool`` separately for diagnostics plus a combined
        ``tool_name`` (``<server>.<tool>``) the CC reducer keys ToolItem on —
        so the memory MCP shows up in the timeline as one tool per call.

        ``id``/``server``/``tool``/``status`` are required (item.rs:302 marks
        them mandatory); ``_require`` surfaces a schema drift as a structured
        ``ProtocolViolationError`` instead of silently degrading to a generic
        ``mcp`` tool. ``arguments`` defaults to JSON null upstream
        (thread_history.rs unwrap_or), so it stays optional here. Provenance
        fields (``appContext`` / ``mcpAppResourceUri`` / ``pluginId``) are
        passed through when present for diagnostics.

        ``started_at`` reads ``params.startedAtMs`` (the notification envelope,
        NOT the item body) — same place ``_command_started_item`` reads it
        from. The 0.144.0 schema does not formally document this field on
        ``item/started``, but real recordings (and the command/fileChange
        translations already rely on it) carry it; pin it with a real
        disposable-thread recording before relying on the value downstream.
        """

        server = _require(item, "server", "item/started(mcpToolCall)")
        tool = _require(item, "tool", "item/started(mcpToolCall)")
        return TranslatedItem(
            type=CodexEventType.TOOL_STARTED,
            thread_id=_as_str(_require(params, "threadId", "item/started")),
            turn_id=_as_str(_require(params, "turnId", "item/started")),
            item_id=_as_str(_require(item, "id", "item/started(mcpToolCall)")),
            payload=immutable_payload(
                kind=_ITEM_MCP_TOOL,
                server=server,
                tool=tool,
                tool_name=_mcp_tool_name(server, tool),
                arguments=item.get("arguments"),
                status=_require(item, "status", "item/started(mcpToolCall)"),
                app_context=item.get("appContext"),
                mcp_app_resource_uri=item.get("mcpAppResourceUri"),
                plugin_id=item.get("pluginId"),
                started_at=params.get("startedAtMs"),
            ),
        )

    def _mcp_tool_completed_item(
        self, params: Mapping[str, Any], item: Mapping[str, Any]
    ) -> TranslatedItem:
        """Build a TOOL_COMPLETED for an ``mcpToolCall`` item (slice-078).

        ``status`` is preserved (``completed`` / ``failed``) so the UI can
        distinguish a failed MCP call from a successful one — the memory MCP
        returns ``dictionary_empty`` as a structured error result, which Codex
        marks ``failed``; that must not paint as a successful read. Same
        required-field + provenance-passthrough stance as the started path.
        """

        server = _require(item, "server", "item/completed(mcpToolCall)")
        tool = _require(item, "tool", "item/completed(mcpToolCall)")
        return TranslatedItem(
            type=CodexEventType.TOOL_COMPLETED,
            thread_id=_as_str(_require(params, "threadId", "item/completed")),
            turn_id=_as_str(_require(params, "turnId", "item/completed")),
            item_id=_as_str(_require(item, "id", "item/completed(mcpToolCall)")),
            payload=immutable_payload(
                kind=_ITEM_MCP_TOOL,
                server=server,
                tool=tool,
                tool_name=_mcp_tool_name(server, tool),
                arguments=item.get("arguments"),
                status=_require(item, "status", "item/completed(mcpToolCall)"),
                result=item.get("result"),
                error=item.get("error"),
                app_context=item.get("appContext"),
                mcp_app_resource_uri=item.get("mcpAppResourceUri"),
                plugin_id=item.get("pluginId"),
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

    # -------------------------------------------------- account / rate limit

    def _on_rate_limits(self, params: Mapping[str, Any]) -> list[TranslatedItem]:
        """``account/rateLimits/updated`` -> RATE_LIMIT_UPDATED (global).

        A sparse rolling update of the account rate-limit snapshot (source:
        ``account.rs:518 AccountRateLimitsUpdatedNotification``). The
        notification has no top-level threadId -- it is account-level, so the
        translated item is addressed to ``thread_id=None``; the manager fans it
        out to active Codex sessions rather than routing by thread.

        Every snapshot field is preserved verbatim in the payload -- the UI
        unfolds only ``used_percent`` / ``resets_at`` / ``rate_limit_reached_type``
        for now (decision 5), but the rest stays so a later UI pass needs no
        schema change. ``spend_control_reached`` is Optional in the protocol and
        absent in the 2026-07-18 recording; ``.get`` surfaces it as ``None``
        rather than fabricating a value (spec C-4: usage null 诚实; same
        principle for any sparse field).
        """

        snapshot = _require(params, "rateLimits", "account/rateLimits/updated")
        if not isinstance(snapshot, Mapping):
            raise ProtocolViolationError(
                "account/rateLimits/updated.rateLimits is not an object",
                payload=dict(params),
            )
        return [
            TranslatedItem(
                type=CodexEventType.RATE_LIMIT_UPDATED,
                payload=immutable_payload(
                    limit_id=snapshot.get("limitId"),
                    limit_name=snapshot.get("limitName"),
                    primary=snapshot.get("primary"),
                    secondary=snapshot.get("secondary"),
                    credits=snapshot.get("credits"),
                    individual_limit=snapshot.get("individualLimit"),
                    spend_control_reached=snapshot.get("spendControlReached"),
                    plan_type=snapshot.get("planType"),
                    rate_limit_reached_type=snapshot.get("rateLimitReachedType"),
                ),
            )
        ]

    # --------------------------------------- slice-077 capability=false skeletons
    # The four handlers below are READY but NOT ROUTED.
    #   * plan / warning  — method sits in _IGNORED_METHODS, so the manager
    #     drops the notification before translate(). Activate by removing the
    #     ignored entry (dispatch is already wired).
    #   * subagent / compaction — item types hit the explicit `return []`
    #     branches in _on_item_started / _on_item_completed. Activate by
    #     replacing the empty return with `[self._xxx_item(params, item)]`.
    # Each activation also needs a real fixture recorded (spec C-1). Fields are
    # sourced from codex 0.144.0 — see slice-077.md §阶段2 协议字段备忘.

    def _on_plan_updated(self, params: Mapping[str, Any]) -> list[TranslatedItem]:
        """``turn/plan/updated`` -> PLAN_UPDATED (capability=false, slice-077).

        Source: ``turn.rs:426 TurnPlanUpdatedNotification``. The model emits
        this when it calls the ``update_plan`` tool — a todo/checklist tool from
        codex-vscode's todo-mcp (``protocol/src/plan_tool.rs``). trowel does not
        configure todo-mcp, so the model never has the tool and this never
        fires today. ``TurnPlanStep`` has no id — upsert key is step text
        (decision 2). Only pending/inProgress/completed; no abandoned
        (decision 1: interrupted plans surface via turn status, not step state).
        """

        plan = _require(params, "plan", "turn/plan/updated")
        if not isinstance(plan, list):
            raise ProtocolViolationError(
                "turn/plan/updated.plan is not an array", payload=dict(params)
            )
        steps = tuple(self._plan_step(raw, "turn/plan/updated") for raw in plan)
        return [
            TranslatedItem(
                type=CodexEventType.PLAN_UPDATED,
                thread_id=_as_str(_require(params, "threadId", "turn/plan/updated")),
                turn_id=_as_str(_require(params, "turnId", "turn/plan/updated")),
                payload=immutable_payload(
                    explanation=params.get("explanation"),
                    steps=steps,
                ),
            )
        ]

    @staticmethod
    def _plan_step(raw: Any, method: str) -> dict[str, Any]:
        """Validate one ``TurnPlanStep`` — ``{step, status}``, no id.

        ``status`` is the ``camelCase`` serialisation of ``TurnPlanStepStatus``
        (``turn.rs:441``): ``pending`` / ``inProgress`` / ``completed``. A value
        outside that set is drift; raise rather than silently coerce.
        """

        if not isinstance(raw, Mapping):
            raise ProtocolViolationError(
                f"notification {method!r} plan step is not an object",
                payload={"raw": raw},
            )
        step = _require(raw, "step", method)
        status = _require(raw, "status", method)
        if status not in ("pending", "inProgress", "completed"):
            raise ProtocolViolationError(
                f"notification {method!r} plan step has unexpected status {status!r}",
                payload=dict(raw),
            )
        return {"step": _as_str(step), "status": _as_str(status)}

    def _subagent_item(
        self, params: Mapping[str, Any], item: Mapping[str, Any]
    ) -> TranslatedItem:
        """``item.type=subAgentActivity`` -> SUBAGENT_ACTIVITY (capability=false).

        Source: ``item.rs:359 SubAgentActivity``. Only Started/Interacted/
        Interrupted — there is no progress/result/error/cancel in the parent
        thread event. usage / summary / per-tool detail require subscribing to
        the sub-thread (``agent_thread_id``); decision 3 keeps usage null here
        (spec C-4: never fabricate ``0 tokens``).
        """

        return TranslatedItem(
            type=CodexEventType.SUBAGENT_ACTIVITY,
            thread_id=_as_str(_require(params, "threadId", "item/*")),
            turn_id=_as_str(_require(params, "turnId", "item/*")),
            item_id=_as_str(item.get("id")),
            payload=immutable_payload(
                kind=item.get("kind"),
                agent_thread_id=item.get("agentThreadId"),
                agent_path=item.get("agentPath"),
            ),
        )

    def _compaction_item(
        self, params: Mapping[str, Any], item: Mapping[str, Any]
    ) -> TranslatedItem:
        """``item.type=contextCompaction`` -> COMPACTION (slice-088 activated).

        Source: ``item.rs:388 ContextCompaction``. The item carries only ``id``;
        pre/post token counts come from ``thread/tokenUsage/updated``. Only the
        ``item/completed`` route calls this (slice-088 / 083 A5); ``item/started``
        stays a no-op — ``thread/compact/start`` returning {} is not a boundary.
        """

        return TranslatedItem(
            type=CodexEventType.COMPACTION,
            thread_id=_as_str(_require(params, "threadId", "item/*")),
            turn_id=_as_str(_require(params, "turnId", "item/*")),
            item_id=_as_str(item.get("id")),
            payload=immutable_payload(),
        )

    def _on_warning(self, params: Mapping[str, Any]) -> list[TranslatedItem]:
        """``warning`` / ``guardianWarning`` -> HOST_WARNING (capability=false).

        Source: ``notification.rs:21 WarningNotification`` (``thread_id``:
        Option) and ``notification.rs:31 GuardianWarningNotification``
        (``thread_id``: String). A stderr-only warning is NOT a turn error
        (decision 6 / spec C-5: never paint a transient warning in the error
        colour or as a terminal state). ``configWarning`` / ``deprecationNotice``
        have different field shapes and stay in ``_IGNORED_METHODS`` until a
        real fixture shows their structure.
        """

        message = _require(params, "message", "warning")
        if not isinstance(message, str):
            # ``_require`` only checks the key exists; a ``null`` here would
            # otherwise become the literal string "None" via ``_as_str`` and
            # render as fake warning copy — the exact fabrication spec C-4
            # forbids. Reject as drift.
            raise ProtocolViolationError(
                "warning.message is not a string", payload=dict(params)
            )
        thread_id_raw = params.get("threadId")
        return [
            TranslatedItem(
                type=CodexEventType.HOST_WARNING,
                thread_id=_as_str(thread_id_raw) if thread_id_raw is not None else None,
                payload=immutable_payload(message=_as_str(message)),
            )
        ]
