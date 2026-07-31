import type {
  AgentSession,
  PermissionPreset,
  Runtime,
  SessionTitleSource,
} from "../../transport/api";
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
  readonly revertEnabled: boolean;
  readonly transportError: string | null;
  abort: AbortController | null;
  readonly connected: boolean;
  readonly sessionKind?: "user" | "delegate";
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
    revertEnabled: session.capabilities.includes("checkpoint"),
    transportError: null,
    abort: null,
    connected: identity.connected,
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
    lastSeq: null,
    needsReplay: false,
    codexSubagents: {},
  };
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
