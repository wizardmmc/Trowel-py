/** 定义统一 Agent HTTP API 的请求、响应类型和调用函数。 */

import type { AgentEvent, Runtime } from "./agentEvent";
import type { GoalStatus } from "./events";
import { transportFetch } from "../../platform/transport";
import { readHttpError } from "./httpError";

export type { Runtime } from "./agentEvent";

export type PermissionPreset =
  "follow" | "read-only" | "workspace-write" | "danger-full-access";

export type SessionTitleSource =
  "new" | "native" | "prompt" | "generated" | "manual";

export type SessionKind = "user" | "delegate" | "probe";

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
  readonly capability_version?: number;
  readonly checkpoint_available?: boolean | null;
  readonly name: string;
  readonly display_title?: string;
  readonly title_source?: SessionTitleSource;
  readonly connected: boolean;
  readonly running: boolean;
  readonly session_kind?: SessionKind;
}

export interface CreateAgentSessionParams {
  readonly runtime: Runtime;
  readonly workdir: string;
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

export interface AgentSessionDefaults {
  readonly runtime: Runtime;
  readonly model: string;
  readonly effort: string;
  readonly permission_mode: string;
  readonly permission_preset?: PermissionPreset;
  readonly memory_enabled: boolean;
  readonly profile_enabled: boolean;
}

export interface AgentHistoryRow {
  readonly runtime: Runtime;
  readonly native_session_id: string | null;
  readonly title: string;
  readonly title_source?: SessionTitleSource;
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
  readonly install_hint?: string | null;
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

export type CodexCommandAction =
  "status" | "compact" | "review" | "goal" | "diff" | "agent";

export interface CodexCommand {
  readonly name: string;
  readonly description: string;
  readonly source: "codex";
  readonly action: CodexCommandAction;
  readonly available_while_running: boolean;
}

export type CodexReviewTarget =
  | { readonly type: "uncommittedChanges" }
  | { readonly type: "baseBranch"; readonly branch: string }
  | { readonly type: "commit"; readonly sha: string; readonly title?: string }
  | { readonly type: "custom"; readonly instructions: string };

export interface CodexReviewStartResult {
  readonly reviewThreadId: string;
  readonly turnId: string;
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
    string | Readonly<Record<string, unknown>>
  )[];
  readonly status: "pending" | "answered" | "expired" | "host_closed";
  readonly decision: string | null;
  readonly auto_resolved: boolean;
  readonly resolution_reason: string | null;
}

export interface AgentSessionCloseResult {
  readonly closed: boolean;
  readonly status: "closed" | "needs_reconcile" | "not_found";
  readonly remaining_resource_count: number;
  readonly remaining_resource_kinds: readonly string[];
  readonly error: string | null;
}

const AGENT_API_BASE = "/api/agent";
const MODEL_CATALOG_TIMEOUT_MS = 5_000;

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
  const response = await transportFetch(url, options);
  if (!response.ok) {
    throw new Error(await readHttpError(response, "Agent API error"));
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

export async function getAgentSession(
  sessionId: string,
): Promise<AgentSession> {
  return request<AgentSession>(`${AGENT_API_BASE}/sessions/${sessionId}`);
}

export async function renameAgentSessionTitle(
  sessionId: string,
  title: string,
): Promise<AgentSession> {
  return request<AgentSession>(
    `${AGENT_API_BASE}/sessions/${sessionId}/title`,
    {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ title }),
    },
  );
}

export async function generateAgentSessionTitle(
  sessionId: string,
  text: string,
): Promise<AgentSession> {
  return request<AgentSession>(
    `${AGENT_API_BASE}/sessions/${sessionId}/title/generate`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text }),
    },
  );
}

export async function deleteAgentSession(
  sessionId: string,
): Promise<AgentSessionCloseResult> {
  return request<AgentSessionCloseResult>(
    `${AGENT_API_BASE}/sessions/${sessionId}`,
    {
      method: "DELETE",
    },
  );
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

export async function listAgentRuntimes(): Promise<
  readonly AgentRuntimeInfo[]
> {
  return request<readonly AgentRuntimeInfo[]>(`${AGENT_API_BASE}/runtimes`);
}

/** 读取可选模型目录，并在 sidecar 未响应时结束等待以免阻塞桌面启动。 */
export async function listAgentModels(): Promise<readonly AgentModel[]> {
  const controller = new AbortController();
  const timeout = setTimeout(
    () => controller.abort(new Error("Codex model catalog request timed out")),
    MODEL_CATALOG_TIMEOUT_MS,
  );
  try {
    const data = await request<{ readonly models: readonly AgentModel[] }>(
      `${AGENT_API_BASE}/models`,
      { signal: controller.signal },
    );
    return data.models;
  } finally {
    clearTimeout(timeout);
  }
}

export async function listCodexCommands(
  sessionId: string,
  signal?: AbortSignal,
): Promise<readonly CodexCommand[]> {
  const data = await request<{ readonly commands: readonly CodexCommand[] }>(
    `${AGENT_API_BASE}/sessions/${sessionId}/commands`,
    { signal },
  );
  return data.commands;
}

export async function compactCodexSession(
  sessionId: string,
): Promise<{ readonly started: boolean }> {
  return request<{ readonly started: boolean }>(
    `${AGENT_API_BASE}/sessions/${sessionId}/commands/compact`,
    { method: "POST" },
  );
}

export async function startCodexReview(
  sessionId: string,
  target: CodexReviewTarget,
): Promise<CodexReviewStartResult> {
  const data = await request<{
    readonly review_thread_id: string;
    readonly turn_id: string;
  }>(`${AGENT_API_BASE}/sessions/${sessionId}/commands/review`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ target }),
  });
  return { reviewThreadId: data.review_thread_id, turnId: data.turn_id };
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

export interface AgentPermissionSelection {
  readonly permission_preset: PermissionPreset;
}

export type CodexGoalStatus = GoalStatus;

export interface CodexGoalSnapshot {
  readonly objective: string;
  readonly status: CodexGoalStatus;
  readonly tokenBudget: number | null;
  readonly tokensUsed: number;
  readonly timeUsedSeconds: number;
  readonly createdAt: number;
  readonly updatedAt: number;
}

interface CodexGoalWire {
  readonly objective: string;
  readonly status: CodexGoalStatus;
  readonly tokenBudget: number | null;
  readonly tokensUsed: number;
  readonly timeUsedSeconds: number;
  readonly createdAt: number;
  readonly updatedAt: number;
}

export interface SetCodexGoalInput {
  readonly objective?: string;
  readonly status?: CodexGoalStatus;
  readonly token_budget?: number | null;
}

function normalizeGoal(goal: CodexGoalWire): CodexGoalSnapshot {
  return { ...goal };
}

export async function getCodexGoal(
  sessionId: string,
): Promise<CodexGoalSnapshot | null> {
  const data = await request<{ goal: CodexGoalWire | null }>(
    `${AGENT_API_BASE}/sessions/${sessionId}/goal`,
  );
  return data.goal ? normalizeGoal(data.goal) : null;
}

export async function setCodexGoal(
  sessionId: string,
  update: SetCodexGoalInput,
): Promise<CodexGoalSnapshot> {
  const data = await request<{ goal: CodexGoalWire }>(
    `${AGENT_API_BASE}/sessions/${sessionId}/goal`,
    {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(update),
    },
  );
  return normalizeGoal(data.goal);
}

export async function clearCodexGoal(
  sessionId: string,
): Promise<{ cleared: boolean }> {
  return request<{ cleared: boolean }>(
    `${AGENT_API_BASE}/sessions/${sessionId}/goal`,
    { method: "DELETE" },
  );
}

export async function startCodexTurn(
  sessionId: string,
  text: string,
): Promise<{ turnId: string }> {
  const data = await request<{ turn_id: string }>(
    `${AGENT_API_BASE}/sessions/${sessionId}/turns`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text }),
    },
  );
  return { turnId: data.turn_id };
}

export async function updateAgentPermissionPreset(
  sessionId: string,
  preset: PermissionPreset,
): Promise<AgentPermissionSelection> {
  return request<AgentPermissionSelection>(
    `${AGENT_API_BASE}/sessions/${sessionId}`,
    {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ permission_preset: preset }),
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
  >(url);
  return {
    rows: result.data ?? [],
    nextCursor: result.meta?.next_cursor ?? null,
  };
}

export function agentMessagesUrl(sessionId: string): string {
  return `${AGENT_API_BASE}/sessions/${sessionId}/messages`;
}

export function agentEventsUrl(sessionId: string): string {
  return `${AGENT_API_BASE}/sessions/${sessionId}/events`;
}

export async function getAgentHistory(
  sessionId: string,
): Promise<readonly AgentEventLike[]> {
  return request<readonly AgentEventLike[]>(
    `${AGENT_API_BASE}/sessions/${sessionId}/history`,
  );
}

export async function getCodexSubagentHistory(
  sessionId: string,
  threadId: string,
): Promise<readonly AgentEventLike[]> {
  return request<readonly AgentEventLike[]>(
    `${AGENT_API_BASE}/sessions/${sessionId}/subagents/${encodeURIComponent(threadId)}/history`,
  );
}

/** history 与 live 共用同一份 AgentEvent wire DTO。 */
export type AgentEventLike = AgentEvent;
