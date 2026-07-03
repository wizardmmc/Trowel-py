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

export interface ErrorEvent {
  readonly type: "error";
  readonly subclass: string;
  readonly errors: readonly string[];
  readonly api_error_status: number | null;
}

export interface InterruptedEvent {
  readonly type: "interrupted";
}

export interface StalledEvent {
  readonly type: "stalled";
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

export type TrowelEvent =
  | SessionStartedEvent
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
  | ThinkingProgressEvent
  | SubagentProgressEvent;

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
