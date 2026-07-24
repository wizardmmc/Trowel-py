import type {
  ApprovalDecision,
  QuestionInput,
  RateLimitSnapshot,
  WorkflowAgentInfo,
  WorkflowPhaseInfo,
  WriteDiff,
} from "../../api/ccTypes";

export type Phase =
  | "idle"
  | "awaiting_first"
  | "thinking"
  | "generating"
  | "tool"
  | "retrying"
  | "compacting"
  | "background_waiting"
  | "awaiting_input"
  | "done"
  | "error"
  | "interrupted";

export type TurnStatus = "active" | "done" | "error" | "interrupted";

export interface ThinkingItem {
  readonly kind: "thinking";
  readonly text: string;
  /** 首个 heartbeat 到 thinking 内容到达之间的秒数。 */
  readonly thinkingDurationSeconds?: number;
}

export interface TextItem {
  readonly kind: "text";
  readonly text: string;
}

export interface ToolItem {
  readonly kind: "tool";
  readonly toolUseId: string;
  readonly toolName: string;
  readonly input: Record<string, unknown>;
  readonly status: "running" | "done" | "failed";
  readonly elapsedSeconds: number | null;
  readonly result: string | null;
  /** 后端从 CC `structuredPatch` 提取的文件差异。 */
  readonly writeDiff?: WriteDiff;
  readonly subagent?: SubagentState;
  /** 子 agent 产生的工具调用树。 */
  readonly childTools: readonly ToolItem[];
  readonly exitCode?: number | null;
  readonly durationMs?: number | null;
  readonly cwd?: string | null;
  readonly nativeStatus?: string | null;
}

export interface SubagentState {
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

/** 找不到对应 Agent 工具时保留的降级展示项。 */
export interface SubagentItem {
  readonly kind: "subagent";
  readonly toolUseId: string;
  readonly subagent: SubagentState;
}

export interface RetryingItem {
  readonly kind: "retrying";
  readonly attempt: number;
  readonly maxRetries: number | null;
  readonly errorStatus: number | null;
  readonly error: string | null;
  readonly retryDelayMs: number | null;
}

export interface CompactBoundaryItem {
  readonly kind: "compact_boundary";
}

export interface LocalCommandItem {
  readonly kind: "local_command";
  readonly content: string;
}

export interface ErrorItem {
  readonly kind: "error";
  readonly subclass: string;
  readonly errors: readonly string[];
  readonly apiErrorStatus: number | null;
}

export interface InterruptedItem {
  readonly kind: "interrupted";
}

export interface ElicitationItem {
  readonly kind: "elicit";
  readonly toolUseId: string;
  readonly requestId: string;
  readonly questions: ReadonlyArray<Readonly<QuestionInput>>;
  readonly status: "pending" | "answered" | "declined";
  readonly resultText: string | null;
  readonly answers: Readonly<Record<string, string>> | null;
}

export interface ApprovalItem {
  readonly kind: "approval";
  readonly requestId: string;
  readonly turnId: string | null;
  readonly itemId: string | null;
  readonly approvalKind: "command_approval" | "file_approval" | "unknown";
  readonly command: string | null;
  readonly cwd: string | null;
  readonly reason: string | null;
  readonly availableDecisions: readonly ApprovalDecision[];
  readonly status: "pending" | "answered" | "expired" | "host_closed";
  readonly decision: string | null;
  readonly autoResolved: boolean;
  readonly resolutionReason: string | null;
}

/** reducer 内部使用的 workflow 快照。 */
export interface WorkflowItem {
  readonly kind: "workflow";
  readonly runId: string;
  readonly taskId: string | null;
  readonly name: string;
  readonly args: string | null;
  readonly status: "running" | "completed" | "killed" | "failed";
  readonly agentCount: number;
  readonly doneCount: number;
  readonly totalTokens: number | null;
  readonly totalToolCalls: number | null;
  readonly durationMs: number | null;
  readonly phases: ReadonlyArray<Readonly<WorkflowPhaseInfo>>;
  readonly agents: ReadonlyArray<Readonly<WorkflowAgentInfo>>;
  readonly error: string | null;
}

export type TurnItem =
  | ThinkingItem
  | TextItem
  | ToolItem
  | SubagentItem
  | RetryingItem
  | CompactBoundaryItem
  | LocalCommandItem
  | ErrorItem
  | InterruptedItem
  | ElicitationItem
  | ApprovalItem
  | WorkflowItem;

export interface Turn {
  readonly id: string;
  readonly userText: string;
  readonly items: readonly TurnItem[];
  readonly status: TurnStatus;
  /** 后端 checkpoint turn_id；history turn 没有 checkpoint。 */
  readonly turnId: string | null;
  readonly revertible: boolean;
  /** 整轮耗时；live 与 history 都归一成秒。 */
  readonly durationSeconds?: number;
  /** live turn 的起始时间，仅用于收到 finished 时计算耗时。 */
  readonly startedAtMs?: number;
}

export interface SessionMeta {
  readonly model: string | null;
  readonly ccSessionId: string | null;
  readonly costUsd: number | null;
  readonly numTurns: number | null;
  readonly hookFired: string | null;
  readonly thinkingStartedAt: number | null;
  readonly thinkingTokens: number | null;
  /** stalled warning 只提示静默状态，不代表进程已经结束。 */
  readonly stallWarning: {
    severity: "mild" | "severe";
    elapsed_s: number;
  } | null;
  readonly exited: boolean;
  readonly exitReturncode: number | null;
  readonly usage: Readonly<Record<string, unknown>> | null;
  readonly hostDegraded: boolean;
  readonly rateLimit: RateLimitSnapshot | null;
}

/** TaskCreate/TaskUpdate 维护的 session 级任务。 */
export interface Task {
  readonly taskId: string | null;
  readonly toolUseId: string;
  readonly subject: string;
  readonly description?: string;
  readonly activeForm?: string;
  readonly status: "pending" | "in_progress" | "completed";
}

export interface ReducerState {
  readonly turns: readonly Turn[];
  readonly phase: Phase;
  readonly meta: SessionMeta;
  readonly tasks: readonly Task[];
}

export const INITIAL_REDUCER_STATE: ReducerState = {
  turns: [],
  phase: "idle",
  tasks: [],
  meta: {
    model: null,
    ccSessionId: null,
    costUsd: null,
    numTurns: null,
    hookFired: null,
    thinkingStartedAt: null,
    thinkingTokens: null,
    stallWarning: null,
    exited: false,
    exitReturncode: null,
    usage: null,
    hostDegraded: false,
    rateLimit: null,
  },
};
