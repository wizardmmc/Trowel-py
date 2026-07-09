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


class AnswerElicitRequest(BaseModel):
    """Body for POST /api/cc/sessions/{id}/answer (slice-025-c).

    The frontend posts the user's AskUserQuestion selections here. `cancel`
    declines the question (writes control_response behavior=deny).
    """

    answers: dict[str, str] = Field(default_factory=dict)
    cancel: bool = False


class RevertRequest(BaseModel):
    """Body for POST /api/cc/sessions/{id}/revert (slice-026 E1).

    turn_id identifies the checkpoint to revert to (drops that turn and every
    later one). It comes from the TurnStartEvent emitted at the start of the
    turn the user is reverting to.
    """

    turn_id: str = Field(min_length=1)


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
        "stalled_warning",
        "thinking_progress",
        "subagent_progress",
        "elicit_request",
        "turn_start",
        "model_changed",
        "workflow_tree",
    }
)


class _Event(BaseModel):
    """Base for every trowel event: carries the discriminator `type`."""

    type: str


class SessionStartedEvent(_Event):
    """Emitted once per CC process after system/init.

    Carries model/cwd/tools plus the bare-name rosters from cc init
    (slash_commands/skills/agents). cc init's lists are z.array(z.string()) —
    names only, no description; the frontend's '/' autocomplete fetches
    descriptions separately from GET /cc/slash-items. Defaults to empty so the
    reducer can treat absent fields (older CC / minimal fixtures) uniformly.
    """

    type: Literal["session_started"] = "session_started"
    model: str
    cwd: str
    cc_session_id: str
    tools: list[str]
    slash_commands: list[str] = Field(default_factory=list)
    skills: list[str] = Field(default_factory=list)
    agents: list[str] = Field(default_factory=list)


class TurnStartEvent(_Event):
    """Emitted at the start of each turn (slice-026 E1).

    Carries the trowel turn_id (the checkpoint ref name) and whether this turn
    is revertible. The frontend attaches turn_id to the optimistic turn it
    already appended, and renders the 'revert to here' button iff revertible.

    Live-only: the history-replay path (parse_history) never emits this —
    replayed turns predate this trowel session and have no checkpoint, so they
    are not revertible (and the frontend shows no button for them).

    revertible is False when the workdir is not a git repo or when the turn
    has no resumable jsonl yet (a fresh session's first turn — cc_session_id
    is only learned from init mid-turn, and there is nothing prior to revert
    to anyway).
    """

    type: Literal["turn_start"] = "turn_start"
    turn_id: str
    revertible: bool


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
    # slice-031: reconstructed think duration in seconds for history replay.
    # The live stream derives this from thinking_tokens heartbeats; the jsonl
    # has none, so history.py back-fills it from entry-timestamp deltas. None
    # when no prev timestamp is available (frontend falls back to bare "思考").
    thinking_duration_seconds: int | None = None


class DiffHunk(BaseModel):
    """One diff hunk — jsdiff StructuredPatchHunk shape (slice-029).

    ``lines`` carry the leading marker char: ``' ctx'``, ``'+add'``, ``'-rm'``.
    Mirrors the FE ``DiffHunk`` interface so both stacks share one wire shape.
    (slice-033 removed the BE-side ``diff_snapshot`` dataclass — cc's own
    structuredPatch is now converted directly in ``tool_use_result.py``.)
    """

    oldStart: int
    oldLines: int
    newStart: int
    newLines: int
    lines: tuple[str, ...]


class WriteDiff(BaseModel):
    """BE-computed diff for a Write tool_use (slice-029 Phase 2).

    ``type='create'`` (new file) → empty hunks; ``type='update'`` → real diff.
    The FE picks render mode off ``type``. Only present on Write tool_call
    events (Edit/MultiEdit diffs are FE-computed from input).
    """

    type: Literal["create", "update"]
    hunks: tuple[DiffHunk, ...]


class ToolCallEvent(_Event):
    """A complete tool_use call (name + full input), emitted when the block closes.

    parent_tool_use_id is set when this tool_use came from a sub-agent (the cc
    envelope carries it, pointing at the spawning Agent tool_call). Null for
    top-level tool_use. The frontend uses it to nest sub-agent tools under their
    Agent (slice-025-a problem 2).
    """

    type: Literal["tool_call"] = "tool_call"
    tool_use_id: str
    tool_name: str
    input: dict[str, Any]
    parent_tool_use_id: str | None = None


class ToolProgressEvent(_Event):
    """A long-running tool is still executing (keeps the stream alive)."""

    type: Literal["tool_progress"] = "tool_progress"
    tool_use_id: str
    tool_name: str
    elapsed_time_seconds: float


class ToolResultEvent(_Event):
    """The result of a tool_use, carried back by CC in a user message.

    write_diff (slice-033 feat 2, 方案 F): present on Edit/MultiEdit/Write
    tool_results — converted from cc's own ``structuredPatch``, which cc
    computes at execution time and carries in the jsonl ``toolUseResult`` /
    stream-json ``tool_use_result`` field. Because cc computed it against the
    real file, the hunk ``oldStart``/``newStart`` are real file line numbers,
    and because it's persisted in jsonl, replay renders identically to live
    even after a BE restart (the old in-memory ``_write_diffs`` cache could
    not). Absent for other tools and for failed/no-op edits — the FE then
    falls back to its fragment diff (line numbers from 1).
    """

    type: Literal["tool_result"] = "tool_result"
    tool_use_id: str
    content: str
    write_diff: WriteDiff | None = None


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


class ModelChangedEvent(_Event):
    """slice-027 C2: emitted right after /model (or /effort) RestartSession so
    the StatusBar syncs immediately. CC is lazy-restarted by the next send's
    _ensure_process, so without this event the model/effort display would lag a
    full turn behind. None fields mean trowel is deferring to cc settings.json
    (no --model / --effort flag passed that turn).
    """

    type: Literal["model_changed"] = "model_changed"
    model: str | None = None
    effort: str | None = None


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


class SessionExitedEvent(_Event):
    """slice-028 bug3: the CC subprocess exited (user typed /exit, or it died
    after the turn). Emitted after FinishedEvent when proc.returncode is set, so
    the frontend can mark the session as exited in the multi-session bar (grey
    out, resumable) and reset the view if it was the active session."""

    type: Literal["session_exited"] = "session_exited"
    returncode: int


class ErrorEvent(_Event):
    """The turn ended in error; subclass distinguishes cause (max_turns, stalled, ...)."""

    type: Literal["error"] = "error"
    subclass: str
    errors: list[str] = Field(default_factory=list)
    api_error_status: int | None = None


class InterruptedEvent(_Event):
    """The user interrupted the current turn (SIGINT)."""

    type: Literal["interrupted"] = "interrupted"


class StalledWarningEvent(_Event):
    """CC has been silent long enough to surface a non-fatal heads-up.

    severity=mild at threshold_mild (120s), severe at threshold_severe (300s).
    The process is NOT killed — on GLM's non-streaming backend, long silence is
    usually legitimate waiting for the first event, not a deadlock. The
    frontend shows a "be patient" / "may be stuck" line under the spinner; the
    user interrupts manually if needed. The 30-min hard cap
    (StalledDetector.threshold_kill) eventually emits ErrorEvent if cc is truly
    wedged.
    """

    type: Literal["stalled_warning"] = "stalled_warning"
    severity: Literal["mild", "severe"]
    elapsed_s: float


class ThinkingProgressEvent(_Event):
    """A thinking-tokens heartbeat: CC is still thinking, with a running token estimate.

    On the GLM backend this is the ONLY signal during thinking — thinking content
    arrives in a later assistant envelope (not via stream deltas), so without these
    heartbeats the frontend would see no event during the whole think and the
    'thinking…' spinner could never trigger. Carries only the cumulative token
    estimate; seconds and verb are computed client-side (see slice-025-a decision #1).
    """

    type: Literal["thinking_progress"] = "thinking_progress"
    estimated_tokens: int


class SubagentProgressEvent(_Event):
    """Progress of a sub-agent spawned via the Agent tool (task_* system events).

    task_started / task_progress / task_notification each carry the tool_use_id of
    the Agent tool_call, so the frontend can attach this to the matching Agent
    ToolItem. task_updated is intentionally NOT mapped: it has no tool_use_id and
    its patch.status duplicates task_notification.status (slice-025-a decision #5).

    Optional fields vary per event: started carries description/subagent_type,
    progress adds last_tool_name/usage, notification carries final usage. The
    frontend merges successive events onto the ToolItem's subagent field.
    """

    type: Literal["subagent_progress"] = "subagent_progress"
    tool_use_id: str
    task_id: str
    status: Literal["started", "progress", "completed"]
    description: str | None = None
    subagent_type: str | None = None
    last_tool_name: str | None = None
    usage: dict[str, Any] | None = None


class ElicitationRequestEvent(_Event):
    """cc asked the user a multiple-choice question via AskUserQuestion (slice-025-c).

    Emitted when translator sees a control_request(can_use_tool) whose
    tool_name is AskUserQuestion. The frontend renders an inline selection
    box (see docs/design/front-end/ask-user-question-20260704.html); the
    user's answers are posted back to the service, which writes a
    control_response(behavior=allow, updatedInput={questions, answers,
    annotations}) to cc stdin. Ground truth: reverse_cc
    samples/raw/052_askuser_bypass_stdio.jsonl (bypass + --permission-prompt-tool
    stdio route — ordinary tools stay silent, only interactive tools trigger this).
    """

    type: Literal["elicit_request"] = "elicit_request"
    tool_use_id: str
    request_id: str
    # questions carried verbatim from control_request.input.questions; the
    # frontend reads {question, header, options:[{label, description?, preview?}],
    # multiSelect} per the mockup field map. Loose dict keeps coupling with cc's
    # evolving schema minimal.
    questions: list[dict[str, Any]]


class WorkflowPhaseInfo(BaseModel):
    """One phase group in a workflow tree (slice-036).

    Sourced from wf_<runId>.json's top-level ``phases`` array (which carries
    both title and detail). Order is the array order — there is no separate
    ``order`` field, and the frontend does NOT render a numeric badge (mockup
    decision: the gold rule + bold title already carry the hierarchy).
    """

    title: str
    detail: str | None = None


class WorkflowAgentInfo(BaseModel):
    """One agent node in a workflow tree (slice-036).

    Sourced from wf_<runId>.json's ``workflowProgress[type="workflow_agent"]``.
    cc already aggregates per-agent tokens/toolCalls/lastToolName here, so trowel
    reads them verbatim (no client-side reduction — slice-036 data-source rule).

    ``state`` is the trowel wire enum (queued/running/done/failed), NOT cc's
    internal one. cc writes ``start``/``progress``/``done``/``error``; the
    watcher normalizes them at parse time (start|progress→running, error→failed)
    so the frontend switches on a stable set regardless of cc renames.
    """

    agent_id: str
    label: str
    phase_index: int | None = None
    phase_title: str | None = None
    model: str | None = None
    state: Literal["queued", "running", "done", "failed"]
    tokens: int | None = None
    tool_calls: int | None = None
    last_tool_name: str | None = None
    duration_ms: int | None = None
    prompt_preview: str | None = None
    result_preview: str | None = None


class WorkflowTreeEvent(_Event):
    """A complete snapshot of one workflow run (slice-036).

    cc runs Workflows in the background and pushes nothing about them to the
    ``--stream-json`` stdout (verified by binary reverse + transcript scan — see
    wiki/raw/2026-07-07-tcc-workflow-render-bug.md). So trowel reads the
    on-disk ``wf_<runId>.json`` cc maintains (the single source of truth cc's
    own TUI also reads), and emits this event. Each push is a FULL snapshot
    (replace, not patch): cc rewrites the whole file on every progress change,
    so a snapshot is simplest and lets the frontend reducer just swap the
    matching run_id.

    Used for BOTH the live path (WorkflowWatcher stat-polls the file while the
    workflow runs) and history replay (history.py scans workflows/wf_*.json) —
    same event shape + same frontend ``WorkflowTree`` component is the C-1
    invariant: reload renders identically to the live completed state.

    Multi-workflow concurrency (C-6): one session may run several workflows;
    each gets its own WorkflowTreeEvent stream keyed by run_id, and the frontend
    renders one card per run_id.
    """

    type: Literal["workflow_tree"] = "workflow_tree"
    run_id: str
    task_id: str | None = None
    name: str
    args: str | None = None
    status: Literal["running", "completed", "killed", "failed"]
    agent_count: int
    """done agents / total — the progress bar (done/total). Computed at parse
    time from the agents list (count of state=='done')."""
    done_count: int
    total_tokens: int | None = None
    total_tool_calls: int | None = None
    duration_ms: int | None = None
    phases: list[WorkflowPhaseInfo] = Field(default_factory=list)
    agents: list[WorkflowAgentInfo] = Field(default_factory=list)
    error: str | None = None


# Union of all trowel events (for type hints; not used as a validator).
TrowelEvent = (
    SessionStartedEvent
    | TurnStartEvent
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
    | SessionExitedEvent
    | ErrorEvent
    | InterruptedEvent
    | StalledWarningEvent
    | ThinkingProgressEvent
    | SubagentProgressEvent
    | ElicitationRequestEvent
    | ModelChangedEvent
    | WorkflowTreeEvent
)
