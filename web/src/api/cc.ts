import type { AnswerElicitBody, TrowelEvent } from "./ccTypes";

const CC_API_BASE = "/api/cc";

export interface CcSessionSummary {
  readonly cc_session_id: string;
  readonly title: string;
  readonly updated_at: number;
}

export interface CcSession {
  readonly session_id: string;
  readonly cc_session_id: string | null;
  readonly model: string;
  readonly name?: string;
  readonly revert_enabled: boolean;
  readonly memory_enabled: boolean;
  readonly profile_enabled: boolean;
}

export interface CreateSessionParams {
  readonly workdir: string;
  readonly resume_from?: string;
  readonly permission_mode?: string;
  readonly model?: string;
  readonly effort?: string;
  readonly memory_enabled?: boolean;
  readonly profile_enabled?: boolean;
}

interface ApiEnvelope<T> {
  readonly success: boolean;
  readonly data: T | null;
  readonly error: string | null;
}

async function request<T>(url: string, options?: RequestInit): Promise<T> {
  const response = await fetch(url, options);
  if (!response.ok) {
    throw new Error(`CC API error: ${response.status}`);
  }
  const result: ApiEnvelope<T> = await response.json();
  if (!result.success || result.error) {
    throw new Error(result.error ?? "CC API call failed");
  }
  return result.data as T;
}

export async function createSession(
  params: CreateSessionParams,
): Promise<CcSession> {
  return request<CcSession>(`${CC_API_BASE}/sessions`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(params),
  });
}

export interface CcSessionListResult {
  readonly sessions: readonly CcSessionSummary[];
  /** 磁盘中的真实总数，用于显示“共 N · 最近 M”。 */
  readonly total: number;
}

export async function listSessions(workdir: string): Promise<CcSessionListResult> {
  const response = await fetch(
    `${CC_API_BASE}/sessions?workdir=${encodeURIComponent(workdir)}`,
  );
  if (!response.ok) {
    throw new Error(`CC API error: ${response.status}`);
  }
  const result: ApiEnvelope<CcSessionSummary[]> & {
    meta?: { total?: number; limit?: number };
  } = await response.json();
  if (!result.success || result.error) {
    throw new Error(result.error ?? "CC API call failed");
  }
  const sessions = result.data ?? [];
  return { sessions, total: result.meta?.total ?? sessions.length };
}

export async function getHistory(sessionId: string): Promise<TrowelEvent[]> {
  return request<TrowelEvent[]>(`${CC_API_BASE}/sessions/${sessionId}/history`);
}

export interface ActiveSession {
  readonly id: string;
  readonly workdir: string;
  readonly model: string;
  readonly name: string;
  readonly running: boolean;
  /** 仅存活 CC 子进程为 true；尚未 spawn 的临时会话不进入多开栏。 */
  readonly connected: boolean;
  readonly memory_enabled: boolean;
  readonly profile_enabled: boolean;
}

export interface ActiveSessionListResult {
  readonly sessions: readonly ActiveSession[];
  readonly activeId: string | null;
}

export async function listActiveSessions(): Promise<ActiveSessionListResult> {
  const data = await request<{
    sessions: readonly ActiveSession[];
    active_id: string | null;
  }>(`${CC_API_BASE}/sessions/active`);
  return { sessions: data.sessions, activeId: data.active_id };
}

export async function activateSession(
  sessionId: string,
): Promise<{ activeId: string }> {
  const data = await request<{ active_id: string }>(
    `${CC_API_BASE}/sessions/${sessionId}/activate`,
    { method: "POST" },
  );
  return { activeId: data.active_id };
}

export async function interruptSession(sessionId: string): Promise<void> {
  await request<{ interrupted: boolean }>(
    `${CC_API_BASE}/sessions/${sessionId}/interrupt`,
    { method: "POST" },
  );
}

export async function revertSession(
  sessionId: string,
  turnId: string,
): Promise<{ reverted_turn_id: string }> {
  return request<{ reverted_turn_id: string }>(
    `${CC_API_BASE}/sessions/${sessionId}/revert`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ turn_id: turnId }),
    },
  );
}

export async function answerElicit(
  sessionId: string,
  body: AnswerElicitBody,
): Promise<{ ok: boolean }> {
  const resp = await fetch(
    `${CC_API_BASE}/sessions/${sessionId}/answer`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    },
  );
  if (!resp.ok) {
    throw new Error(`CC API error: ${resp.status}`);
  }
  const result: ApiEnvelope<{ answered: boolean }> = await resp.json();
  return { ok: Boolean(result.success) };
}

export async function deleteSession(sessionId: string): Promise<void> {
  await request<{ closed: boolean }>(`${CC_API_BASE}/sessions/${sessionId}`, {
    method: "DELETE",
  });
}

export interface ModelOption {
  readonly value: string;
  readonly label: string;
  readonly real_model: string;
  readonly description: string;
  readonly is_default?: boolean;
}

export async function listModels(): Promise<readonly ModelOption[]> {
  return request<readonly ModelOption[]>(`${CC_API_BASE}/models`);
}

export interface SlashItem {
  readonly name: string;
  readonly description: string;
  readonly source: "project" | "user" | "bundled" | "builtin" | "plugin";
  readonly type: "skill" | "command";
}

export async function listSlashItems(
  workdir: string,
): Promise<readonly SlashItem[]> {
  return request<readonly SlashItem[]>(
    `${CC_API_BASE}/slash-items?workdir=${encodeURIComponent(workdir)}`,
  );
}

export interface DirEntry {
  readonly name: string;
  readonly path: string;
}

export async function listDir(path: string): Promise<readonly DirEntry[]> {
  return request<readonly DirEntry[]>(
    `${CC_API_BASE}/list-dir?path=${encodeURIComponent(path)}`,
  );
}

export function messagesUrl(sessionId: string): string {
  return `${CC_API_BASE}/sessions/${sessionId}/messages`;
}
