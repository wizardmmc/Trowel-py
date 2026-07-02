"""Pydantic models for the cc_host slice.

Two groups:
- Request models: bodies for the HTTP endpoints.
- Trowel event models: the ONLY event contract the frontend consumes. CC's raw
  stream-json events are translated into these before leaving the server (see
  trowel_py/cc_host/translator.py). The frontend never sees raw CC events.

Every event carries a literal `type` discriminator so the frontend can switch
on it without guessing.
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------


class CreateSessionRequest(BaseModel):
    """Body for POST /api/cc/sessions.

    Attributes:
        workdir: working directory the CC subprocess runs in (its cwd), so CC
            loads that project's .claude/ hooks/memory/skills.
        resume_from: optional CC session id to resume (--resume <id>).
        permission_mode: CC --permission-mode. Defaults to bypassPermissions
            for a smooth, non-interrupting experience (v1 has no permission UI).
        model: override --model (defaults handled by launcher).
        effort: override --effort (defaults handled by launcher).
    """

    workdir: str = Field(min_length=1)
    resume_from: str | None = None
    permission_mode: str = "bypassPermissions"
    model: str | None = None
    effort: str | None = None


class SendMessageRequest(BaseModel):
    """Body for POST /api/cc/sessions/{id}/messages."""

    text: str = Field(min_length=1)


# ---------------------------------------------------------------------------
# Trowel event models (frontend wire contract)
# ---------------------------------------------------------------------------

# Discriminator strings, one per trowel event kind.
EVENT_TYPES = frozenset(
    {
        "session_started",
        "user",
        "text",
        "thinking",
        "tool_call",
        "tool_progress",
        "tool_result",
        "retrying",
        "hook",
        "status",
        "compact_boundary",
        "local_command",
        "finished",
        "error",
        "interrupted",
        "stalled",
    }
)


class _Event(BaseModel):
    """Base for every trowel event: carries the discriminator `type`."""

    type: str


class SessionStartedEvent(_Event):
    """Emitted once per CC process after system/init (model, cwd, tools)."""

    type: Literal["session_started"] = "session_started"
    model: str
    cwd: str
    cc_session_id: str
    tools: list[str]


class UserEvent(_Event):
    """A user text message — history-replay only.

    The live stream never carries user text as an event: the frontend appends
    the user's own message optimistically when it sends. But when replaying a
    past CC session (GET /sessions/{id}/history), the user's messages must
    surface somehow, and reusing the same reducer is a hard spec constraint.
    So the history translator emits this event for each historical user turn;
    it never appears on the live SSE stream.
    """

    type: Literal["user"] = "user"
    text: str


class TextEvent(_Event):
    """One assistant text delta (a fragment of the streaming reply)."""

    type: Literal["text"] = "text"
    text: str


class ThinkingEvent(_Event):
    """One thinking delta, so the UI can show 'thinking...' instead of idling."""

    type: Literal["thinking"] = "thinking"
    text: str


class ToolCallEvent(_Event):
    """A complete tool_use call (name + full input), emitted when the block closes."""

    type: Literal["tool_call"] = "tool_call"
    tool_use_id: str
    tool_name: str
    input: dict[str, Any]


class ToolProgressEvent(_Event):
    """A long-running tool is still executing (keeps the stream alive)."""

    type: Literal["tool_progress"] = "tool_progress"
    tool_use_id: str
    tool_name: str
    elapsed_time_seconds: float


class ToolResultEvent(_Event):
    """The result of a tool_use, carried back by CC in a user message."""

    type: Literal["tool_result"] = "tool_result"
    tool_use_id: str
    content: str


class RetryingEvent(_Event):
    """GLM/backend retry in progress (transparently shown, CC manages it)."""

    type: Literal["retrying"] = "retrying"
    attempt: int
    max_retries: int | None = None
    error_status: int | None = None
    error: str | None = None
    retry_delay_ms: int | None = None


class HookEvent(_Event):
    """A workdir hook fired (proves the project's .claude/ hooks are active)."""

    type: Literal["hook"] = "hook"
    hook_name: str
    outcome: str | None = None


class StatusEvent(_Event):
    """A phase transition (e.g. 'compacting'), so the UI does not read silence as a hang."""

    type: Literal["status"] = "status"
    stage: str


class CompactBoundaryEvent(_Event):
    """CC finished an auto-compact pass on its context."""

    type: Literal["compact_boundary"] = "compact_boundary"


class LocalCommandEvent(_Event):
    """Output from a trowel-handled local command (/cost, /status) or unsupported slash."""

    type: Literal["local_command"] = "local_command"
    content: str


class FinishedEvent(_Event):
    """The turn completed successfully; carries usage/cost for accounting and /cost."""

    type: Literal["finished"] = "finished"
    usage: dict[str, Any]
    total_cost_usd: float
    num_turns: int


class ErrorEvent(_Event):
    """The turn ended in error; subclass distinguishes cause (max_turns, stalled, ...)."""

    type: Literal["error"] = "error"
    subclass: str
    errors: list[str] = Field(default_factory=list)
    api_error_status: int | None = None


class InterruptedEvent(_Event):
    """The user interrupted the current turn (SIGINT)."""

    type: Literal["interrupted"] = "interrupted"


class StalledEvent(_Event):
    """CC went silent past the threshold (informational; service emits ErrorEvent on the second one)."""

    type: Literal["stalled"] = "stalled"


# Union of all trowel events (for type hints; not used as a validator).
TrowelEvent = (
    SessionStartedEvent
    | UserEvent
    | TextEvent
    | ThinkingEvent
    | ToolCallEvent
    | ToolProgressEvent
    | ToolResultEvent
    | RetryingEvent
    | HookEvent
    | StatusEvent
    | CompactBoundaryEvent
    | LocalCommandEvent
    | FinishedEvent
    | ErrorEvent
    | InterruptedEvent
    | StalledEvent
)
