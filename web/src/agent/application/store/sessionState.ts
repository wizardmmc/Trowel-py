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
  type ReducerState,
} from "../../domain/reducer";

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
