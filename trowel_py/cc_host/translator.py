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
from trowel_py.schemas.cc_host import (
    CompactBoundaryEvent,
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
                )
            ]
        if sub == "api_retry":
            self._retry_attempt += 1
            return [
                RetryingEvent(
                    attempt=self._retry_attempt,
                    max_retries=ev.get("max_retries"),
                    error_status=ev.get("error_status"),
                    error=ev.get("error"),
                    retry_delay_ms=ev.get("retry_delay_ms"),
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
        out: list[TrowelEvent] = []
        for block in ev.get("message", {}).get("content", []) or []:
            if block.get("type") != "tool_result":
                continue
            out.append(
                ToolResultEvent(
                    tool_use_id=block.get("tool_use_id", ""),
                    content=_as_text(block.get("content")),
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
