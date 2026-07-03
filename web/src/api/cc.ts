/**
 * REST client for /api/cc — CC session CRUD + history replay.
 *
 * Reuses the same envelope shape ({success, data, error}) as client.ts but on
 * the /api/cc base. Streaming (POST /messages) lives in ccStream.ts because it
 * needs a ReadableStream body, not a JSON response.
 */
import type { TrowelEvent } from "./ccTypes";

const CC_API_BASE = "http://localhost:8000/api/cc";

/** One row of GET /api/cc/sessions?workdir=... — a resumable history entry. */
export interface CcSessionSummary {
  readonly cc_session_id: string;
  readonly title: string;
  /** epoch seconds (file mtime) */
  readonly updated_at: number;
}

/** Response of POST /api/cc/sessions — a freshly opened (or resumed) session. */
export interface CcSession {
  readonly session_id: string;
  /** null until the CC process actually starts and reports its session id */
  readonly cc_session_id: string | null;
  readonly model: string;
}

export interface CreateSessionParams {
  readonly workdir: string;
  readonly resume_from?: string;
  readonly permission_mode?: string;
  readonly model?: string;
  readonly effort?: string;
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

/** POST /api/cc/sessions — open a new session or resume a past one. */
export async function createSession(
  params: CreateSessionParams,
): Promise<CcSession> {
  return request<CcSession>(`${CC_API_BASE}/sessions`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(params),
  });
}

/** Result of GET /api/cc/sessions — the capped list + the true on-disk total. */
export interface CcSessionListResult {
  readonly sessions: readonly CcSessionSummary[];
  /** total sessions on disk (meta.total) — for "共 N · 最近 M" display. */
  readonly total: number;
}

/** GET /api/cc/sessions?workdir=... — list most-recent history sessions + total. */
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

/** GET /api/cc/sessions/{id}/history — replay a session's stored events. */
export async function getHistory(sessionId: string): Promise<TrowelEvent[]> {
  return request<TrowelEvent[]>(`${CC_API_BASE}/sessions/${sessionId}/history`);
}

/** POST /api/cc/sessions/{id}/interrupt — SIGINT the current turn. */
export async function interruptSession(sessionId: string): Promise<void> {
  await request<{ interrupted: boolean }>(
    `${CC_API_BASE}/sessions/${sessionId}/interrupt`,
    { method: "POST" },
  );
}

/** DELETE /api/cc/sessions/{id} — kill the subprocess and drop the session. */
export async function deleteSession(sessionId: string): Promise<void> {
  await request<{ closed: boolean }>(`${CC_API_BASE}/sessions/${sessionId}`, {
    method: "DELETE",
  });
}

/** URL for POST /messages — passed to ccStream.postMessageStream. */
export function messagesUrl(sessionId: string): string {
  return `${CC_API_BASE}/sessions/${sessionId}/messages`;
}
