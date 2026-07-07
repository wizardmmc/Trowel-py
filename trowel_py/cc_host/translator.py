"""Translate raw CC stream-json events into trowel events.

This module is the hard decoupling boundary: the frontend only ever consumes
trowel events, never CC's raw events. Create one Translator per user turn
(per send()); it owns a DeltaAccumulator and per-turn counters.

CC event shapes are taken from the spike ground truth
(spikes/cc-input-probe.py / cc-skill-trigger-probe.py actually parsed these
fields). A few events (status / thinking_tokens / compact_boundary) have
shapes the spec marks "verify at impl time" — those are handled best-effort
and flagged with TODO comments.
"""

from __future__ import annotations

import logging
from typing import Any

from trowel_py.cc_host.delta import DeltaAccumulator
from trowel_py.cc_host.tool_use_result import write_diff_from_cc_result
from trowel_py.schemas.cc_host import (
    CompactBoundaryEvent,
    ElicitationRequestEvent,
    ErrorEvent,
    FinishedEvent,
    HookEvent,
    LocalCommandEvent,
    RetryingEvent,
    SessionStartedEvent,
    StatusEvent,
    SubagentProgressEvent,
    TextEvent,
    ThinkingEvent,
    ThinkingProgressEvent,
    ToolCallEvent,
    ToolProgressEvent,
    ToolResultEvent,
    TrowelEvent,
)

# system subtypes / top-level types that v1 explicitly does not map.
# The spec calls these out as "not needed by the frontend".
_IGNORE_SYSTEM_SUBTYPES = frozenset(
    {
        "post_turn_summary",
        # task_updated has no tool_use_id and its patch.status duplicates
        # task_notification.status — slice-025-a decision #5 keeps it ignored.
        "task_updated",
        "session_state_changed",
        "files_persisted",
        "elicitation_complete",
        "prompt_suggestion",
        "mcp_message",
    }
)
_IGNORE_SYSTEM_PREFIXES = ("streamlined_",)

_RESULT_ERROR_SUBCLASSES = frozenset(
    {
        "error_during_execution",
        "error_max_turns",
        "error_max_budget_usd",
        "error_max_structured_output_retries",
    }
)

# Tools whose tool_use is rendered inline as a selection box via the
# control_request path (ElicitationRequestEvent) rather than as a tool block.
# Mirrors cc terminal's renderToolUseMessage=null for these tools (slice-025-c).
# Future: EnterPlanMode / ExitPlanMode will join this set.
_ELICIT_TOOL_NAMES = frozenset({"AskUserQuestion"})


def _is_elicit_tool(name: str) -> bool:
    """True for interactive tools rendered via the elicitation path."""
    return name in _ELICIT_TOOL_NAMES


logger = logging.getLogger(__name__)


class Translator:
    """Stateful per-turn translator: CC event dict -> list[TrowelEvent]."""

    def __init__(self) -> None:
        """Initialize per-turn state: a fresh accumulator, retry counter, and dispatch table."""
        self._acc = DeltaAccumulator()
        self._retry_attempt = 0
        self._emitted_tool_ids: set[str] = set()
        self._dispatch = {
            "system": self._on_system,
            "stream_event": self._on_stream_event,
            "assistant": self._on_assistant,
            "user": self._on_user,
            "tool_progress": self._on_tool_progress,
            "result": self._on_result,
            "control_request": self._on_control_request,
        }

    def translate(self, cc_event: dict[str, Any]) -> list[TrowelEvent]:
        """Translate one raw CC event into zero or more trowel events.

        Dispatches on the CC event's top-level `type`. Unknown types and the
        explicitly-ignored subtypes yield an empty list.

        Args:
            cc_event: one raw CC stream-json event dict (top-level type + fields).

        Returns:
            the trowel events to emit for this CC event; empty when there is
            nothing to map (unknown type, ignored subtype).
        """
        top_type = cc_event.get("type")
        handler = self._dispatch.get(top_type)
        if handler is None:
            return []
        return handler(cc_event)

    # -- per-type handlers -------------------------------------------------

    def _on_system(self, ev: dict[str, Any]) -> list[TrowelEvent]:
        """Handle a CC `system` event (metadata/control).

        `ev` is one raw CC event dict. The `subtype` field selects the concrete
        meaning: init -> session_started, api_retry -> retrying, hook_* -> hook,
        status/compact_boundary/local_command_output mapped 1:1. Unknown
        subtypes are dropped and logged at debug level for diagnosis.

        Args:
            ev: the raw CC `system` event dict.

        Returns:
            trowel events for this system event (often a single event, or empty
            for ignored/unknown subtypes).
        """
        sub = ev.get("subtype")
        if sub in _IGNORE_SYSTEM_SUBTYPES:
            return []
        if isinstance(sub, str) and any(
            sub.startswith(p) for p in _IGNORE_SYSTEM_PREFIXES
        ):
            return []
        if sub == "init":
            return [
                SessionStartedEvent(
                    model=ev.get("model", ""),
                    cwd=ev.get("cwd", ""),
                    cc_session_id=ev.get("session_id", ""),
                    tools=list(ev.get("tools", [])),
                    slash_commands=list(ev.get("slash_commands", [])),
                    skills=list(ev.get("skills", [])),
                    agents=list(ev.get("agents", [])),
                )
            ]
        if sub == "api_retry":
            self._retry_attempt += 1
            return [
                RetryingEvent(
                    attempt=self._retry_attempt,
                    max_retries=_as_int(ev.get("max_retries")),
                    error_status=_as_int(ev.get("error_status")),
                    error=ev.get("error"),
                    retry_delay_ms=_as_int(ev.get("retry_delay_ms")),
                )
            ]
        if sub in ("hook_started", "hook_response"):
            return [
                HookEvent(
                    hook_name=ev.get("hook_name", ""),
                    outcome=ev.get("outcome"),
                )
            ]
        if sub == "status":
            # TODO(verify): exact field name for the stage. Spec example is
            # subtype2="compacting"; fall back to subtype2 then a generic label.
            stage = ev.get("subtype2") or ev.get("stage") or "unknown"
            return [StatusEvent(stage=stage)]
        if sub == "compact_boundary":
            return [CompactBoundaryEvent()]
        if sub == "local_command_output":
            return [LocalCommandEvent(content=_as_text(ev.get("content")))]
        if sub == "thinking_tokens":
            # GLM backend: the ONLY signal during thinking (content arrives in a
            # later assistant envelope). Slice-025-a A1 uses this as the heartbeat.
            return [
                ThinkingProgressEvent(
                    estimated_tokens=int(ev.get("estimated_tokens", 0)),
                )
            ]
        if sub == "task_started":
            return [
                SubagentProgressEvent(
                    tool_use_id=ev.get("tool_use_id", ""),
                    task_id=ev.get("task_id", ""),
                    status="started",
                    description=ev.get("description"),
                    subagent_type=ev.get("subagent_type"),
                )
            ]
        if sub == "task_progress":
            return [
                SubagentProgressEvent(
                    tool_use_id=ev.get("tool_use_id", ""),
                    task_id=ev.get("task_id", ""),
                    status="progress",
                    description=ev.get("description"),
                    subagent_type=ev.get("subagent_type"),
                    last_tool_name=ev.get("last_tool_name"),
                    usage=ev.get("usage"),
                )
            ]
        if sub == "task_notification":
            return [
                SubagentProgressEvent(
                    tool_use_id=ev.get("tool_use_id", ""),
                    task_id=ev.get("task_id", ""),
                    status="completed",
                    usage=ev.get("usage"),
                )
            ]
        logger.debug("unmapped CC system subtype dropped: %r", sub)
        return []

    def _on_stream_event(self, ev: dict[str, Any]) -> list[TrowelEvent]:
        """Handle a CC `stream_event` (Anthropic-protocol streaming inner event).

        Routes on the inner `event.type`: content_block_start opens a block in
        the accumulator, content_block_delta yields text/thinking or feeds a
        tool input fragment, content_block_stop closes a block (and may emit a
        tool_call). The overall stream does NOT end here — that is the top-level
        `result` event.

        Args:
            ev: the raw CC `stream_event` dict (its `event` field holds the
                Anthropic streaming event).

        Returns:
            trowel events for this inner event (text/thinking deltas, or a
            tool_call on block stop); empty for start/fragment events.
        """
        inner = ev.get("event", {})
        itype = inner.get("type")
        if itype == "content_block_start":
            self._acc.on_block_start(
                inner.get("index", 0), inner.get("content_block", {})
            )
            return []
        if itype == "content_block_delta":
            return self._on_delta(inner)
        if itype == "content_block_stop":
            return self._on_block_stop(inner.get("index", 0))
        return []

    def _on_delta(self, inner: dict[str, Any]) -> list[TrowelEvent]:
        """Handle a content_block_delta inner event, by delta content type.

        text_delta -> a TextEvent fragment; thinking_delta -> a ThinkingEvent
        fragment; input_json_delta -> a tool_use input fragment handed to the
        accumulator (no event yet — emitted when the block closes).

        Args:
            inner: the Anthropic content_block_delta event (its `delta.type`
                selects the branch).

        Returns:
            zero or one trowel event (TextEvent / ThinkingEvent); empty for an
            input_json_delta fragment.
        """
        delta = inner.get("delta", {})
        dtype = delta.get("type")
        index = inner.get("index", 0)
        if dtype == "text_delta":
            return [TextEvent(text=delta.get("text", ""))]
        if dtype == "thinking_delta":
            return [ThinkingEvent(text=delta.get("thinking", delta.get("text", "")))]
        if dtype == "input_json_delta":
            self._acc.on_input_json_delta(index, delta.get("partial_json", ""))
            return []
        return []

    def _on_block_stop(self, index: int) -> list[TrowelEvent]:
        """Close a streaming block; emit a tool_call if it was a tool_use.

        De-duplicates against previously emitted tool_use ids so a tool_call
        streamed here is not re-emitted from the later assistant envelope.

        Args:
            index: the content block index (per Anthropic streaming protocol).

        Returns:
            a single ToolCallEvent for a completed tool_use block, or empty for
            text/thinking blocks and already-emitted tool_use ids.
        """
        result = self._acc.on_block_stop(index)
        if result is None:
            return []
        if result.tool_use_id in self._emitted_tool_ids:
            return []
        if _is_elicit_tool(result.tool_name):
            # Rendered inline as a selection box via the control_request path
            # (ElicitationRequestEvent); skip the ToolCallEvent so it doesn't
            # double-render as a tool block. Mirrors cc terminal
            # renderToolUseMessage=null (slice-025-c).
            return []
        self._emitted_tool_ids.add(result.tool_use_id)
        return [
            ToolCallEvent(
                tool_use_id=result.tool_use_id,
                tool_name=result.tool_name,
                input=result.input,
            )
        ]

    def _on_assistant(self, ev: dict[str, Any]) -> list[TrowelEvent]:
        """Handle a CC `assistant` envelope (a complete assistant message).

        The envelope carries fully-assembled content blocks. On the glm backend
        CC emits content ONLY via these envelopes (no stream_event deltas — see
        spikes/e1-stream-capture.py ground truth), so this is where text,
        thinking, and tool_use are all surfaced. tool_use is de-duplicated by
        id against anything already emitted from stream deltas (a future/other
        backend may still stream them); text/thinking have no id and emit 1:1.

        Args:
            ev: the raw CC `assistant` event dict (message.content blocks).

        Returns:
            one event per content block: TextEvent / ThinkingEvent / ToolCallEvent.
        """
        out: list[TrowelEvent] = []
        for block in ev.get("message", {}).get("content", []) or []:
            if not isinstance(block, dict):
                continue
            kind = block.get("type")
            if kind == "text":
                out.append(TextEvent(text=str(block.get("text", ""))))
            elif kind == "thinking":
                out.append(
                    ThinkingEvent(
                        text=str(block.get("thinking", block.get("text", "")))
                    )
                )
            elif kind == "tool_use":
                tid = block.get("id", "")
                if not tid or tid in self._emitted_tool_ids:
                    continue
                if _is_elicit_tool(block.get("name", "")):
                    # Rendered via control_request → ElicitationRequestEvent;
                    # skip the tool block (slice-025-c).
                    continue
                self._emitted_tool_ids.add(tid)
                out.append(
                    ToolCallEvent(
                        tool_use_id=tid,
                        tool_name=block.get("name", ""),
                        input=block.get("input") or {},
                        parent_tool_use_id=ev.get("parent_tool_use_id"),
                    )
                )
        return out

    def _on_user(self, ev: dict[str, Any]) -> list[TrowelEvent]:
        """Handle a CC `user` envelope, extracting tool_result blocks.

        CC echoes tool execution results back as a user message whose content
        blocks are tool_result entries. Plain user-text echoes (no tool_result)
        yield nothing.

        Args:
            ev: the raw CC `user` event dict (message.content blocks).

        Returns:
            one ToolResultEvent per tool_result block; empty otherwise.
        """
        # slice-033 feat 2 (方案 F): the stream-json `user` envelope carries
        # cc's pre-computed diff in its top-level `tool_use_result` field (same
        # shape as jsonl's `toolUseResult`). Attach to the tool_result so the FE
        # renders real file line numbers on the live path too.
        write_diff = write_diff_from_cc_result(ev.get("tool_use_result"))
        out: list[TrowelEvent] = []
        for block in ev.get("message", {}).get("content", []) or []:
            if block.get("type") != "tool_result":
                continue
            out.append(
                ToolResultEvent(
                    tool_use_id=block.get("tool_use_id", ""),
                    content=_as_text(block.get("content")),
                    write_diff=write_diff,
                )
            )
        return out

    def _on_tool_progress(self, ev: dict[str, Any]) -> list[TrowelEvent]:
        """Handle a CC `tool_progress` event (a long-running tool is still busy).

        Args:
            ev: the raw CC `tool_progress` dict (tool_use_id, tool_name, elapsed).

        Returns:
            a single ToolProgressEvent (keeps the stream visibly alive).
        """
        return [
            ToolProgressEvent(
                tool_use_id=ev.get("tool_use_id", ""),
                tool_name=ev.get("tool_name", ""),
                elapsed_time_seconds=float(ev.get("elapsed_time_seconds", 0.0)),
            )
        ]

    def _on_result(self, ev: dict[str, Any]) -> list[TrowelEvent]:
        """Handle the terminal CC `result` event (ends a turn) and reset state.

        Maps known error subtypes -> ErrorEvent, success -> FinishedEvent (also
        captured for /cost accounting by the service), anything else -> a
        generic ErrorEvent. Always resets the delta accumulator for next turn.

        Args:
            ev: the raw CC `result` dict (subtype, is_error, usage, cost, ...).

        Returns:
            exactly one terminal event — FinishedEvent or ErrorEvent.
        """
        sub = ev.get("subtype")
        if sub in _RESULT_ERROR_SUBCLASSES:
            errors_raw = ev.get("errors") or []
            self._acc.reset()
            return [
                ErrorEvent(
                    subclass=sub,
                    errors=[str(e) for e in errors_raw]
                    if isinstance(errors_raw, list)
                    else [str(errors_raw)],
                    api_error_status=ev.get("api_error_status"),
                )
            ]
        if sub == "success" and not ev.get("is_error"):
            self._acc.reset()
            return [
                FinishedEvent(
                    usage=ev.get("usage") or {},
                    total_cost_usd=float(ev.get("total_cost_usd", 0.0)),
                    num_turns=int(ev.get("num_turns", 0)),
                )
            ]
        # Generic error result (not a known subclass) — surface as error too.
        self._acc.reset()
        return [
            ErrorEvent(
                subclass=sub or "error", api_error_status=ev.get("api_error_status")
            )
        ]

    def _on_control_request(self, ev: dict[str, Any]) -> list[TrowelEvent]:
        """Handle a CC `control_request` (slice-025-c interactive tool path).

        Under bypassPermissions + `--permission-prompt-tool stdio`, cc emits
        control_request(can_use_tool) only for requiresUserInteraction tools
        (AskUserQuestion/EnterPlanMode/ExitPlanMode) — ordinary tools stay
        silent because permissions.ts 1e sits before the 2a bypass short-circuit
        (see reverse_cc spec/04 D2-实证段). We translate AskUserQuestion into
        ElicitationRequestEvent so the frontend renders an inline selection box;
        every other control_request is left for a future slice (permission
        confirmation UI) and yields nothing for now.

        Args:
            ev: the raw CC `control_request` dict (request_id + request{subtype,
                tool_name, input, tool_use_id}).

        Returns:
            one ElicitationRequestEvent for AskUserQuestion; empty otherwise.
        """
        req = ev.get("request") or {}
        if req.get("subtype") != "can_use_tool":
            return []
        if req.get("tool_name") != "AskUserQuestion":
            return []
        tool_use_id = req.get("tool_use_id")
        request_id = ev.get("request_id")
        if not tool_use_id or not request_id:
            # Without these the host cannot match the control_response back to
            # cc's request — drop and warn rather than emit a half-formed event
            # (CR WARNING: silent "" coercion masked protocol errors).
            logger.warning(
                "AskUserQuestion control_request missing tool_use_id or "
                "request_id; dropping. raw=%s",
                ev,
            )
            return []
        questions = (req.get("input") or {}).get("questions") or []
        return [
            ElicitationRequestEvent(
                tool_use_id=tool_use_id,
                request_id=request_id,
                questions=list(questions),
            )
        ]


def _as_text(content: Any) -> str:
    """Flatten CC content into a single string.

    Args:
        content: a string, or a list of content blocks (only {type:text,...}
            blocks contribute their text), or None.

    Returns:
        the concatenated text (blocks joined by newlines); "" for None.
    """
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = [
            b.get("text", "")
            for b in content
            if isinstance(b, dict) and b.get("type") == "text"
        ]
        return "\n".join(parts)
    return str(content)


def _as_int(value: Any) -> int | None:
    """Coerce a raw CC field to int, or None if absent.

    The GLM backend sometimes emits fractional floats for fields the CC
    schema declares as int (e.g. ``retry_delay_ms=574.04``); Pydantic v2
    rejects those with ``int_from_float``, so we truncate at this boundary.
    Booleans are rejected — bool is an int subclass but never a meaningful
    count here, so a stray bool is treated as "no value".

    Args:
        value: the raw CC field value (int, float, str, None, ...).

    Returns:
        the value as an int, or None when absent / unparsable.
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
