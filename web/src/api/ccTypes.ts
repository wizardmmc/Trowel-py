
/**
 * 前端消费的统一事件类型，不直接暴露 Claude Code 或 Codex 的原始事件。
 * 字段必须与后端 adapter 的真实输出保持一致。
 */
export interface SessionStartedEvent {
  readonly type: "session_started";
  readonly model: string;
  readonly cwd: string;
  readonly cc_session_id: string;
  readonly tools: readonly string[];
  readonly slash_commands?: readonly string[];
  readonly skills?: readonly string[];
  readonly agents?: readonly string[];
}

export interface TurnStartEvent {
  readonly type: "turn_start";
  readonly turn_id: string;
  readonly revertible: boolean;
}

export interface UserEvent {
  readonly type: "user";
  readonly text: string;
  readonly duration_seconds?: number;
}

export interface TextEvent {
  readonly type: "text";
  readonly text: string;
}

export interface ThinkingEvent {
  readonly type: "thinking";
  readonly text: string;
  readonly thinking_duration_seconds?: number;
}

export interface ToolCallEvent {
  readonly type: "tool_call";
  readonly tool_use_id: string;
  readonly tool_name: string;
  readonly input: Record<string, unknown>;
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
  readonly content: string | null;
  readonly write_diff?: WriteDiff;
  readonly exit_code?: number | null;
  readonly duration_ms?: number | null;
  readonly cwd?: string | null;
  readonly command?: string | null;
  readonly status?: string | null;
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

export interface StalledWarningEvent {
  readonly type: "stalled_warning";
  readonly severity: "mild" | "severe";
  readonly elapsed_s: number;
}

export interface ThinkingProgressEvent {
  readonly type: "thinking_progress";
  readonly estimated_tokens: number;
}

export interface SubagentProgressEvent {
  readonly type: "subagent_progress";
  readonly tool_use_id: string;
  readonly task_id: string;
  readonly status:
    | "started"
    | "progress"
    | "completed"
    | "failed"
    | "cancelled"
    | "unknown";
  readonly description?: string | null;
  readonly subagent_type?: string | null;
  readonly last_tool_name?: string | null;
  readonly usage?: Record<string, unknown> | null;
}

export interface ElicitationRequestEvent {
  readonly type: "elicit_request";
  readonly tool_use_id: string;
  readonly request_id: string;
  readonly questions: ReadonlyArray<Readonly<QuestionInput>>;
}

export interface ModelChangedEvent {
  readonly type: "model_changed";
  readonly model: string | null;
  readonly effort: string | null;
}

export interface WorkflowAgentInfo {
  readonly agent_id: string;
  readonly label: string;
  readonly phase_index: number | null;
  readonly phase_title: string | null;
  readonly model: string | null;
  readonly state: "queued" | "running" | "done" | "failed";
  readonly tokens: number | null;
  readonly tool_calls: number | null;
  readonly last_tool_name: string | null;
  readonly duration_ms: number | null;
  readonly prompt_preview: string | null;
  readonly result_preview: string | null;
}

export interface WorkflowPhaseInfo {
  readonly title: string;
  readonly detail: string | null;
}

export interface WorkflowTreeEvent {
  readonly type: "workflow_tree";
  readonly run_id: string;
  readonly task_id: string | null;
  readonly name: string;
  readonly args: string | null;
  readonly status: "running" | "completed" | "killed" | "failed";
  readonly agent_count: number;
  readonly done_count: number;
  readonly total_tokens: number | null;
  readonly total_tool_calls: number | null;
  readonly duration_ms: number | null;
  readonly phases: ReadonlyArray<Readonly<WorkflowPhaseInfo>>;
  readonly agents: ReadonlyArray<Readonly<WorkflowAgentInfo>>;
  readonly error: string | null;
}

export interface TokenUsageBreakdown {
  readonly totalTokens?: number | null;
  readonly inputTokens?: number | null;
  readonly cachedInputTokens?: number | null;
  readonly outputTokens?: number | null;
  readonly reasoningOutputTokens?: number | null;
}

export interface UsageUpdatedEvent {
  readonly type: "usage_updated";
  readonly total?: Readonly<TokenUsageBreakdown> | null;
  readonly last?: Readonly<TokenUsageBreakdown> | null;
  readonly model_context_window?: number | null;
}

export interface HostStatusEvent {
  readonly type: "host_status";
  readonly status: "ready" | "degraded" | "host_exited";
  readonly reason?: string | null;
  readonly exit_code?: number | null;
}

export interface RateLimitWindow {
  readonly usedPercent: number | null;
  readonly windowDurationMins: number | null;
  readonly resetsAt: number | null;
}

export type RateLimitReachedType =
  | "rate_limit_reached"
  | "workspace_owner_credits_depleted"
  | "workspace_member_credits_depleted"
  | "workspace_owner_usage_limit_reached"
  | "workspace_member_usage_limit_reached"
  | (string & {});

export interface RateLimitSnapshot {
  readonly limit_id: string | null;
  readonly limit_name: string | null;
  readonly primary: Readonly<RateLimitWindow> | null;
  readonly secondary: Readonly<RateLimitWindow> | null;
  readonly credits: Readonly<Record<string, unknown>> | null;
  readonly individual_limit: Readonly<Record<string, unknown>> | null;
  readonly spend_control_reached: Readonly<Record<string, unknown>> | null;
  readonly plan_type: string | null;
  readonly rate_limit_reached_type: RateLimitReachedType | null;
}

export interface RateLimitUpdatedEvent {
  readonly type: "rate_limit_updated";
  readonly limit_id: string | null;
  readonly limit_name: string | null;
  readonly primary: Readonly<RateLimitWindow> | null;
  readonly secondary: Readonly<RateLimitWindow> | null;
  readonly credits: Readonly<Record<string, unknown>> | null;
  readonly individual_limit: Readonly<Record<string, unknown>> | null;
  readonly spend_control_reached: Readonly<Record<string, unknown>> | null;
  readonly plan_type: string | null;
  readonly rate_limit_reached_type: RateLimitReachedType | null;
}

export type ApprovalDecision = string | Readonly<Record<string, unknown>>;

export interface ApprovalRequestEvent {
  readonly type: "approval_request";
  readonly turn_id?: string;
  readonly request_id: string;
  readonly item_id: string | null;
  readonly approval_kind: "command_approval" | "file_approval" | "unknown";
  readonly command: string | null;
  readonly cwd: string | null;
  readonly reason: string | null;
  readonly available_decisions: readonly ApprovalDecision[];
  readonly status: "pending" | "answered" | "expired" | "host_closed";
  readonly decision: string | null;
  readonly auto_resolved: boolean;
  readonly resolution_reason: string | null;
}

export interface QuestionInput {
  readonly question: string;
  readonly header: string;
  readonly multiSelect: boolean;
  readonly options: ReadonlyArray<QuestionOption>;
  readonly annotations?: { preview?: string; notes?: string };
}

export interface QuestionOption {
  readonly label: string;
  readonly description?: string;
  readonly preview?: string;
}

export interface AnswerElicitBody {
  readonly answers: Readonly<Record<string, string>>;
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
  | ModelChangedEvent
  | WorkflowTreeEvent
  | UsageUpdatedEvent
  | HostStatusEvent
  | ApprovalRequestEvent
  | RateLimitUpdatedEvent;

export const RECOVERABLE_ERROR_SUBCLASSES = new Set([
  "error_during_execution",
]);

export const TERMINAL_ERROR_SUBCLASSES = new Set([
  "error_max_turns",
  "error_max_budget_usd",
  "error_max_structured_output_retries",
]);

export interface DiffHunk {
  readonly oldStart: number;
  readonly oldLines: number;
  readonly newStart: number;
  readonly newLines: number;
  readonly lines: readonly string[];
}

export interface WriteDiff {
  readonly type: "create" | "update" | "delete";
  readonly hunks: readonly DiffHunk[];
}
