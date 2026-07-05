/**
 * Trowel CC event contract — the ONLY event types the frontend consumes.
 *
 * Mirrors `trowel_py/schemas/cc_host.py` 1:1. The frontend never imports CC's
 * raw stream-json types (slice023-web invariant: "不耦合 CC 原始"). Every event
 * carries a literal `type` discriminator so a switch narrows it with no guess.
 *
 * `UserEvent` (type "user") is history-replay only — the live SSE stream never
 * carries user text, but GET /sessions/{id}/history emits it so the same
 * reducer renders history and live.
 */

export interface SessionStartedEvent {
  readonly type: "session_started";
  readonly model: string;
  readonly cwd: string;
  readonly cc_session_id: string;
  readonly tools: readonly string[];
  /** slice-027 C1: bare-name rosters from cc init (z.array(z.string())) —
   * the '/' autocomplete floor. Optional: older CC / minimal fixtures may omit.
   * Descriptions are NOT here; they come from GET /cc/slash-items. */
  readonly slash_commands?: readonly string[];
  readonly skills?: readonly string[];
  readonly agents?: readonly string[];
}

/** Emitted at the start of each live turn (slice-026 E1). Carries the backend
 * turn_id (the checkpoint ref name) and whether this turn is revertible. The
 * reducer attaches both to the optimistic turn the store already created.
 * History-replay never emits this — replayed turns predate this trowel session
 * and have no checkpoint, so they are not revertible. */
export interface TurnStartEvent {
  readonly type: "turn_start";
  readonly turn_id: string;
  readonly revertible: boolean;
}

export interface UserEvent {
  readonly type: "user";
  readonly text: string;
}

export interface TextEvent {
  readonly type: "text";
  readonly text: string;
}

export interface ThinkingEvent {
  readonly type: "thinking";
  readonly text: string;
}

export interface ToolCallEvent {
  readonly type: "tool_call";
  readonly tool_use_id: string;
  readonly tool_name: string;
  readonly input: Record<string, unknown>;
  /** Set when this tool_use came from a sub-agent's envelope — points at the
   * spawning Agent tool_call's id. Null/absent for top-level tool_use. */
  readonly parent_tool_use_id?: string | null;
}

export interface ToolProgressEvent {
  readonly type: "tool_progress";
  readonly tool_use_id: string;
  readonly tool_name: string;
  readonly elapsed_time_seconds: number;
}

export interface ToolResultEvent {
  readonly type: "tool_result";
  readonly tool_use_id: string;
  readonly content: string;
}

export interface RetryingEvent {
  readonly type: "retrying";
  readonly attempt: number;
  readonly max_retries: number | null;
  readonly error_status: number | null;
  readonly error: string | null;
  readonly retry_delay_ms: number | null;
}

export interface HookEvent {
  readonly type: "hook";
  readonly hook_name: string;
  readonly outcome: string | null;
}

export interface StatusEvent {
  readonly type: "status";
  readonly stage: string;
}

export interface CompactBoundaryEvent {
  readonly type: "compact_boundary";
}

export interface LocalCommandEvent {
  readonly type: "local_command";
  readonly content: string;
}

export interface FinishedEvent {
  readonly type: "finished";
  readonly usage: Record<string, unknown>;
  readonly total_cost_usd: number;
  readonly num_turns: number;
}

/** slice-028 bug3: the CC subprocess exited (user typed /exit, or it died after
 * a turn). Emitted after FinishedEvent when `proc.returncode` is set. The
 * frontend marks the session as exited in the multi-session bar (greyed out,
 * resumable) and, if it was the active session, returns the view to the
 * "no active session" state. Mirrors `SessionExitedEvent` in cc_host.py. */
export interface SessionExitedEvent {
  readonly type: "session_exited";
  readonly returncode: number;
}

export interface ErrorEvent {
  readonly type: "error";
  readonly subclass: string;
  readonly errors: readonly string[];
  readonly api_error_status: number | null;
}

export interface InterruptedEvent {
  readonly type: "interrupted";
}

/** CC has been silent long enough for a non-fatal heads-up (the process is NOT
 * killed — on GLM's non-streaming backend long silence is usually legitimate
 * waiting, so stall detection now phases a heads-up rather than auto-killing).
 * severity=mild at 120s, severe at 300s. The frontend shows a "be patient" /
 * "may be stuck" line under the spinner; the user interrupts manually. The
 * 30-min hard cap surfaces as ErrorEvent(subclass="stalled"), not this event.
 * Mirrors `StalledWarningEvent` in cc_host.py. */
export interface StalledWarningEvent {
  readonly type: "stalled_warning";
  readonly severity: "mild" | "severe";
  readonly elapsed_s: number;
}

/** A thinking-tokens heartbeat (slice-025-a A1). On the GLM backend this is the
 * only signal during thinking. Seconds/verb are client-side; only the cumulative
 * token estimate rides the event. */
export interface ThinkingProgressEvent {
  readonly type: "thinking_progress";
  readonly estimated_tokens: number;
}

/** Sub-agent (Agent tool) progress, translated from task_started/progress/
 * notification (slice-025-a A3). task_updated is intentionally not mapped. */
export interface SubagentProgressEvent {
  readonly type: "subagent_progress";
  readonly tool_use_id: string;
  readonly task_id: string;
  readonly status: "started" | "progress" | "completed";
  readonly description?: string | null;
  readonly subagent_type?: string | null;
  readonly last_tool_name?: string | null;
  readonly usage?: Record<string, unknown> | null;
}

/** AskUserQuestion interactive prompt (slice-025-c). Translated from cc's
 * control_request(can_use_tool, tool_name=AskUserQuestion) — bypass +
 * --permission-prompt-tool stdio route. The frontend renders an inline
 * selection box (see docs/design/front-end/ask-user-question-20260704.html);
 * the user's answers are posted to POST /api/cc/sessions/:id/answer. */
export interface ElicitationRequestEvent {
  readonly type: "elicit_request";
  readonly tool_use_id: string;
  readonly request_id: string;
  /** questions carried verbatim from cc — each has {question, header,
   * options:[{label, description?, preview?}], multiSelect}. Loose typing
   * keeps coupling with cc's evolving schema minimal (mirror of the python
   * ElicitationRequestEvent). */
  readonly questions: ReadonlyArray<Readonly<QuestionInput>>;
}

/** slice-027 C2: emitted right after /model or /effort RestartSession so the
 * StatusBar syncs immediately. CC is lazy-restarted by the next send, so
 * without this the model/effort display would lag a full turn behind. null
 * fields mean trowel is deferring to cc settings.json (no flag passed). */
export interface ModelChangedEvent {
  readonly type: "model_changed";
  readonly model: string | null;
  readonly effort: string | null;
}

/** One question in an AskUserQuestion elicitation (spec/04 A.1). */
export interface QuestionInput {
  readonly question: string;
  readonly header: string;
  readonly multiSelect: boolean;
  readonly options: ReadonlyArray<QuestionOption>;
  readonly annotations?: { preview?: string; notes?: string };
}

/** One option within a question. */
export interface QuestionOption {
  readonly label: string;
  readonly description?: string;
  readonly preview?: string;
}

/** Answer payload for POST /api/cc/sessions/:id/answer. */
export interface AnswerElicitBody {
  /** {questionText: answerStr}; multi-select answers are comma-separated. */
  readonly answers: Readonly<Record<string, string>>;
  /** true = decline (writes control_response behavior=deny). */
  readonly cancel: boolean;
}

export type TrowelEvent =
  | SessionStartedEvent
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
  | ModelChangedEvent;

/** Error subclasses that are recoverable — the "retry last" button is enabled. */
export const RECOVERABLE_ERROR_SUBCLASSES = new Set([
  "error_during_execution",
]);

/** Error subclasses that mean "CC hit a hard stop" — no retry, only guidance. */
export const TERMINAL_ERROR_SUBCLASSES = new Set([
  "error_max_turns",
  "error_max_budget_usd",
  "error_max_structured_output_retries",
]);
