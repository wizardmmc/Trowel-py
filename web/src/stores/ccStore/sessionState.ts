import type {
  AgentSession,
  Runtime,
} from "../../api/agent";
import {
  INITIAL_REDUCER_STATE,
  type ReducerState,
} from "../ccReducer";

/** 单个会话的 reducer 状态，以及 store 管理的身份和传输字段。 */
export interface PerSessionState extends ReducerState {
  readonly workdir: string;
  readonly effort: string | null;
  readonly name: string;
  readonly revertEnabled: boolean;
  readonly transportError: string | null;
  abort: AbortController | null;
  readonly connected: boolean;
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
  readonly capabilities: readonly string[];
  readonly lastSeq: number | null;
  readonly needsReplay: boolean;
}

/** 新建会话参数；未指定 runtime 时沿用 Claude Code。 */
export interface StartSessionParams {
  readonly workdir: string;
  readonly runtime?: Runtime;
  readonly resume_from?: string;
  readonly model?: string;
  readonly effort?: string;
  readonly permission_mode?: string;
  readonly approval_policy?: string;
  readonly sandbox?: string;
  readonly permission_preset?:
    | "follow"
    | "read-only"
    | "workspace-write"
    | "danger-full-access";
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
    revertEnabled: session.capabilities.includes("checkpoint"),
    transportError: null,
    abort: null,
    connected: identity.connected,
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
    capabilities: session.capabilities,
    lastSeq: null,
    needsReplay: false,
  };
}

function basename(workdir: string): string {
  return workdir.split("/").pop() || workdir;
}
