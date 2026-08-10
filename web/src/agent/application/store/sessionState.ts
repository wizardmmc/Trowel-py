/** 定义 application store 持有的单会话状态及后端会话映射。 */

import type {
  AgentSession,
  AgentResourceState,
  AgentTurnState,
  PermissionPreset,
  Runtime,
  SessionKind,
  SessionTitleSource,
} from "../../transport/api";
import type { AgentTransportProblem } from "../../transport/httpError";
import {
  INITIAL_REDUCER_STATE,
  reduceEvent,
  type ReducerState,
} from "../../domain/reducer";
import { finalizeRunningTools } from "../../domain/reducer/terminal";

export interface CodexSubagentThread {
  readonly threadId: string;
  readonly parentThreadId: string;
  readonly agentPath: string | null;
  readonly status: "started" | "progress" | "completed" | "failed" | "cancelled";
  readonly state: ReducerState;
  readonly historyLoaded: boolean;
  readonly historyLoading: boolean;
  readonly historyError: string | null;
}

/** 单个会话的 reducer 状态，以及 store 管理的身份和传输字段。 */
export interface PerSessionState extends ReducerState {
  readonly workdir: string;
  readonly effort: string | null;
  readonly name: string;
  readonly displayTitle: string;
  readonly titleSource: SessionTitleSource;
  readonly checkpointAvailable: boolean | null;
  readonly transportError: string | null;
  readonly transportProblem?: AgentTransportProblem | null;
  abort: AbortController | null;
  readonly connected: boolean;
  readonly resourceState: AgentResourceState;
  readonly turnState: AgentTurnState;
  readonly liveState: "ready" | "reconnecting" | "gapped" | "unavailable";
  readonly currentTurnId: string | null;
  readonly stateGeneration: number;
  readonly sessionKind?: SessionKind;
  readonly memoryEnabled: boolean;
  readonly profileEnabled: boolean;
  readonly runtime: Runtime;
  readonly connectionId?: string | null;
  readonly connectionName?: string | null;
  readonly connectionKind?: string | null;
  readonly nativeSessionId: string | null;
  readonly permission: string | null;
  readonly permissionPreset?: string | null;
  readonly effectivePermissionProfile?: string | null;
  readonly effectiveSandbox?: string | null;
  readonly effectiveApproval?: string | null;
  readonly networkAccess?: boolean | null;
  readonly pendingModel?: string | null;
  readonly pendingEffort?: string | null;
  readonly settingsNotice?: string | null;
  readonly commandPending?: "compact" | "review" | null;
  readonly capabilities: readonly string[];
  readonly lastSeq: number | null;
  readonly needsReplay: boolean;
  readonly codexSubagents: Readonly<Record<string, CodexSubagentThread>>;
}

/** transport 文案与结构化问题是一个逻辑状态，成功路径必须成对清除。 */
export const CLEARED_TRANSPORT_ISSUE = Object.freeze({
  transportError: null,
  transportProblem: null,
});

/** 记录没有结构化 problem 的旧式错误时，显式丢弃此前已失效的 problem。 */
export function transportIssueFromMessage(transportError: string) {
  return { transportError, transportProblem: null } as const;
}

/** 让旧文案字段与结构化 problem 始终指向同一次失败。 */
export function transportIssueFromProblem(problem: AgentTransportProblem) {
  return { transportError: problem.message, transportProblem: problem } as const;
}

/** 新建会话参数；未指定 runtime 时沿用 Claude Code。 */
export interface StartSessionParams {
  readonly workdir: string;
  readonly runtime?: Runtime;
  readonly connection_id?: string;
  readonly resume_from?: string;
  readonly resume_title?: string;
  readonly model?: string;
  readonly effort?: string;
  readonly permission_mode?: string;
  readonly approval_policy?: string;
  readonly sandbox?: string;
  readonly permission_preset?: PermissionPreset;
  readonly memory_enabled?: boolean;
  readonly profile_enabled?: boolean;
  /** 配置连接尚未通过 MCP 真实 Gate，生产新建流程固定关闭。 */
  readonly agent_mcp_enabled?: boolean;
}

/** 把新建接口返回值映射为尚未连接的前端会话。 */
export function createNewSessionState(
  session: AgentSession,
  params: StartSessionParams,
): PerSessionState {
  return createSessionState(session, {
    workdir: params.workdir,
    effort: params.effort ?? null,
    name: session.name ?? basename(params.workdir),
    model: session.model ?? params.model ?? null,
    connected: false,
  });
}

/** 把刷新接口返回值映射为后端当前记录的会话。 */
export function createReconciledSessionState(
  session: AgentSession,
): PerSessionState {
  return createSessionState(session, {
    workdir: session.workdir,
    effort: session.effort,
    name: session.name,
    model: session.model,
    connected: session.connected,
  });
}

/**
 * 按活动快照补出当前根 turn 容器。
 *
 * history 可能暂时为空或尚未封口，但 currentTurnId 与 turnState 是后端当前事实；
 * 每次历史回放完成后都要再次执行本规则，才能承接随后恢复的审批和终态事件。
 */
export function materializeCurrentRootTurn(
  session: PerSessionState,
): PerSessionState {
  if (!session.currentTurnId || !isRootTurnInFlight(session)) return session;
  const existingIndex = session.turns.findIndex(
    (turn) => turn.turnId === session.currentTurnId,
  );
  if (existingIndex !== -1) {
    const existing = session.turns[existingIndex];
    if (existing.status === "active") return session;
    return {
      ...session,
      turns: session.turns.map((turn, index) =>
        index === existingIndex ? { ...turn, status: "active" as const } : turn,
      ),
      phase: phaseForActiveTurn(session.turnState),
    };
  }
  const materialized = reduceEvent(session, {
    type: "turn_start",
    turn_id: session.currentTurnId,
    autonomous: true,
    revertible: false,
  });
  return {
    ...session,
    ...materialized,
    phase: phaseForActiveTurn(session.turnState),
  };
}

/**
 * 用活动快照把当前根 turn 的容器和终态一起收敛。
 *
 * 后端可能先持久化终态，再由下一次活动快照通知 renderer；当事件序号没有变化时，
 * 不会触发 history replay，因此这里必须独立结束仍为 active 的 reducer turn。
 */
export function reconcileCurrentRootTurn(
  session: PerSessionState,
): PerSessionState {
  if (isRootTurnInFlight(session)) return materializeCurrentRootTurn(session);
  const terminal = ROOT_TURN_TERMINALS[session.turnState];
  if (!terminal || !session.currentTurnId) return session;
  const currentIndex = session.turns.findIndex(
    (turn) => turn.turnId === session.currentTurnId,
  );
  if (currentIndex === -1 || session.turns[currentIndex].status !== "active") {
    return session;
  }
  return {
    ...session,
    turns: session.turns.map((turn, index) =>
      index === currentIndex
        ? {
            ...turn,
            status: terminal.status,
            items: finalizeRunningTools(turn.items),
            startedAtMs: undefined,
          }
        : turn,
    ),
    phase: terminal.phase,
    abort: null,
    commandPending: null,
  };
}

const ROOT_TURN_TERMINALS: Partial<
  Record<AgentTurnState, {
    readonly status: "done" | "error" | "interrupted";
    readonly phase: "done" | "error" | "interrupted";
  }>
> = {
  completed: { status: "done", phase: "done" },
  failed: { status: "error", phase: "error" },
  interrupted: { status: "interrupted", phase: "interrupted" },
};

/** 把后端活动 turn 状态映射为重建容器后的 renderer 阶段。 */
function phaseForActiveTurn(turnState: AgentTurnState): ReducerState["phase"] {
  if (turnState === "awaiting_input") return "awaiting_input";
  if (turnState === "starting") return "awaiting_first";
  return "generating";
}

interface SessionIdentity {
  readonly workdir: string;
  readonly effort: string | null;
  readonly name: string;
  readonly model: string | null;
  readonly connected: boolean;
}

function createSessionState(
  session: AgentSession,
  identity: SessionIdentity,
): PerSessionState {
  return {
    ...INITIAL_REDUCER_STATE,
    meta: { ...INITIAL_REDUCER_STATE.meta, model: identity.model },
    workdir: identity.workdir,
    effort: identity.effort,
    name: identity.name,
    displayTitle: session.display_title ?? "",
    titleSource: session.title_source ?? "new",
    checkpointAvailable: session.checkpoint_available ?? null,
    ...CLEARED_TRANSPORT_ISSUE,
    abort: null,
    connected: identity.connected,
    resourceState: session.resource_state ?? "connected",
    turnState:
      session.turn_state ?? (session.running ? "running" : "idle"),
    liveState: "reconnecting",
    currentTurnId: session.current_turn_id ?? null,
    stateGeneration: session.state_generation ?? 0,
    sessionKind: session.session_kind ?? "user",
    memoryEnabled: session.memory_enabled,
    profileEnabled: session.profile_enabled,
    runtime: session.runtime,
    connectionId: session.connection_id ?? null,
    connectionName: session.connection_name ?? null,
    connectionKind: session.connection_kind ?? null,
    nativeSessionId: session.native_session_id,
    permission: session.permission,
    permissionPreset: session.permission_preset,
    effectivePermissionProfile: session.effective_permission_profile,
    effectiveSandbox: session.effective_sandbox,
    effectiveApproval: session.effective_approval,
    networkAccess: session.network_access,
    pendingModel: null,
    pendingEffort: null,
    settingsNotice: null,
    commandPending: null,
    capabilities: session.capabilities,
    lastSeq: session.last_event_seq ?? null,
    needsReplay: false,
    codexSubagents: {},
  };
}

/** 判断根 turn 是否仍禁止发送下一条用户输入。 */
export function isRootTurnInFlight(session: PerSessionState): boolean {
  return (
    session.turnState === "starting" ||
    session.turnState === "running" ||
    session.turnState === "awaiting_input" ||
    session.turnState === "unknown"
  );
}

function basename(workdir: string): string {
  return workdir.split("/").pop() || workdir;
}

/** 把首条用户消息压成会话栏可立即显示的单行标题。 */
export function promptSessionTitle(text: string): string {
  const normalized = text.replace(/\s+/g, " ").trim();
  if (normalized.length <= 80) return normalized;
  return `${normalized.slice(0, 79).trimEnd()}…`;
}
