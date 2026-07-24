
export type Runtime = "claude_code" | "codex";

export interface AgentSession {
  readonly session_id: string;
  readonly runtime: Runtime;
  readonly native_session_id: string | null;
  readonly workdir: string;
  readonly model: string | null;
  readonly effort: string | null;
  readonly permission: string | null;
  readonly permission_preset?: string | null;
  readonly effective_permission_profile?: string | null;
  readonly effective_sandbox?: string | null;
  readonly effective_approval?: string | null;
  readonly network_access?: boolean | null;
  readonly memory_enabled: boolean;
  readonly profile_enabled: boolean;
  readonly capabilities: readonly string[];
  readonly name: string;
  readonly connected: boolean;
  readonly running: boolean;
}

export interface CreateAgentSessionParams {
  readonly runtime: Runtime;
  readonly workdir: string;
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

export interface AgentSessionDefaults {
  readonly runtime: Runtime;
  readonly model: string;
  readonly effort: string;
  readonly permission_mode: string;
  readonly permission_preset?:
    | "follow"
    | "read-only"
    | "workspace-write"
    | "danger-full-access";
  readonly memory_enabled: boolean;
  readonly profile_enabled: boolean;
}

export interface AgentHistoryRow {
  readonly runtime: Runtime;
  readonly native_session_id: string | null;
  readonly title: string;
  readonly updated_at: number | string;
}

export interface AgentHistoryPage {
  readonly rows: readonly AgentHistoryRow[];
  readonly nextCursor: string | null;
}

export interface AgentRuntimeInfo {
  readonly runtime: Runtime;
  readonly label: string;
  readonly native: string;
  readonly capabilities: readonly string[];
  readonly connected: boolean;
}

export interface AgentEffort {
  readonly value: string;
  readonly description: string;
}

export interface AgentModel {
  readonly id: string;
  readonly model: string;
  readonly display_name: string;
  readonly description: string;
  readonly is_default: boolean;
  readonly default_effort: string;
  readonly supported_efforts: readonly AgentEffort[];
}

export interface AgentSettingsSelection {
  readonly model: string;
  readonly effort: string;
  readonly adjusted: boolean;
}

export interface AgentPendingRequest {
  readonly request_id: string;
  readonly session_id: string;
  readonly thread_id: string;
  readonly turn_id: string | null;
  readonly item_id: string | null;
  readonly approval_kind: "command_approval" | "file_approval" | "unknown";
  readonly command: string | null;
  readonly cwd: string | null;
  readonly reason: string | null;
  readonly available_decisions: readonly (
    | string
    | Readonly<Record<string, unknown>>
  )[];
  readonly status: "pending" | "answered" | "expired" | "host_closed";
  readonly decision: string | null;
  readonly auto_resolved: boolean;
  readonly resolution_reason: string | null;
}

const AGENT_API_BASE = "/api/agent";

interface ApiEnvelope<T, M = unknown> {
  readonly success: boolean;
  readonly data: T | null;
  readonly meta?: M;
  readonly error: string | null;
}

async function requestEnvelope<T, M = unknown>(
  url: string,
  options?: RequestInit,
): Promise<ApiEnvelope<T, M>> {
  const response = await fetch(url, options);
  if (!response.ok) {
    throw new Error(`Agent API error: ${response.status}`);
  }
  const result: ApiEnvelope<T, M> = await response.json();
  if (!result.success || result.error) {
    throw new Error(result.error ?? "Agent API call failed");
  }
  return result;
}

async function request<T>(url: string, options?: RequestInit): Promise<T> {
  const result = await requestEnvelope<T>(url, options);
  return result.data as T;
}

export async function createAgentSession(
  params: CreateAgentSessionParams,
): Promise<AgentSession> {
  return request<AgentSession>(`${AGENT_API_BASE}/sessions`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(params),
  });
}

export async function getAgentSessionDefaults(): Promise<AgentSessionDefaults | null> {
  return request<AgentSessionDefaults | null>(
    `${AGENT_API_BASE}/session-defaults`,
  );
}

export interface ActiveAgentListResult {
  readonly sessions: readonly AgentSession[];
  readonly activeId: string | null;
}

export async function listActiveAgentSessions(): Promise<ActiveAgentListResult> {
  const data = await request<{
    sessions: readonly AgentSession[];
    active_id: string | null;
  }>(`${AGENT_API_BASE}/sessions/active`);
  return { sessions: data.sessions, activeId: data.active_id };
}

export async function activateAgentSession(
  sessionId: string,
): Promise<{ activeId: string }> {
  const data = await request<{ active_id: string }>(
    `${AGENT_API_BASE}/sessions/${sessionId}/activate`,
    { method: "POST" },
  );
  return { activeId: data.active_id };
}

export async function getAgentSession(sessionId: string): Promise<AgentSession> {
  return request<AgentSession>(`${AGENT_API_BASE}/sessions/${sessionId}`);
}

export async function deleteAgentSession(
  sessionId: string,
): Promise<{ closed: boolean }> {
  return request<{ closed: boolean }>(`${AGENT_API_BASE}/sessions/${sessionId}`, {
    method: "DELETE",
  });
}

export async function interruptAgentSession(
  sessionId: string,
): Promise<{ interrupted: boolean }> {
  return request<{ interrupted: boolean }>(
    `${AGENT_API_BASE}/sessions/${sessionId}/interrupt`,
    { method: "POST" },
  );
}

export async function answerAgentRequest(
  sessionId: string,
  requestId: string,
  decision: string,
): Promise<{ answered: boolean; request: AgentPendingRequest }> {
  return request<{ answered: boolean; request: AgentPendingRequest }>(
    `${AGENT_API_BASE}/sessions/${sessionId}/requests/${encodeURIComponent(requestId)}/answer`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ decision }),
    },
  );
}

export async function listAgentRequests(
  sessionId: string,
): Promise<readonly AgentPendingRequest[]> {
  const data = await request<{ requests: readonly AgentPendingRequest[] }>(
    `${AGENT_API_BASE}/sessions/${sessionId}/requests`,
  );
  return data.requests;
}

export async function listAgentRuntimes(): Promise<readonly AgentRuntimeInfo[]> {
  return request<readonly AgentRuntimeInfo[]>(`${AGENT_API_BASE}/runtimes`);
}

export async function listAgentModels(): Promise<readonly AgentModel[]> {
  const data = await request<{ readonly models: readonly AgentModel[] }>(
    `${AGENT_API_BASE}/models`,
  );
  return data.models;
}

export async function updateAgentSessionSettings(
  sessionId: string,
  selection: { readonly model: string; readonly effort: string },
): Promise<AgentSettingsSelection> {
  return request<AgentSettingsSelection>(
    `${AGENT_API_BASE}/sessions/${sessionId}`,
    {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(selection),
    },
  );
}

export async function listAgentHistory(
  workdir: string,
  options: { readonly limit?: number; readonly cursor?: string | null } = {},
): Promise<AgentHistoryPage> {
  const limit = options.limit ?? 20;
  let url = `${AGENT_API_BASE}/sessions?workdir=${encodeURIComponent(workdir)}&limit=${limit}`;
  if (options.cursor) url += `&cursor=${encodeURIComponent(options.cursor)}`;
  const result = await requestEnvelope<
    readonly AgentHistoryRow[],
    { readonly limit: number; readonly next_cursor: string | null }
  >(
    url,
  );
  return {
    rows: result.data ?? [],
    nextCursor: result.meta?.next_cursor ?? null,
  };
}

export function agentMessagesUrl(sessionId: string): string {
  return `${AGENT_API_BASE}/sessions/${sessionId}/messages`;
}

export async function getAgentHistory(
  sessionId: string,
): Promise<readonly AgentEventLike[]> {
  return request<readonly AgentEventLike[]>(
    `${AGENT_API_BASE}/sessions/${sessionId}/history`,
  );
}

/** 只声明 history 回放所需字段，避免与 agentTypes 形成运行时循环依赖。 */
export interface AgentEventLike {
  readonly schema: "agent-event-v1";
  readonly session_id: string;
  readonly runtime: Runtime;
  readonly seq: number;
  readonly type: string;
  readonly turn_id: string | null;
  readonly item_id: string | null;
  readonly payload: Readonly<Record<string, unknown>>;
}
