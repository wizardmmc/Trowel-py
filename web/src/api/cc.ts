/**
 * REST client for /api/cc — CC session CRUD + history replay.
 *
 * Reuses the same envelope shape ({success, data, error}) as client.ts but on
 * the /api/cc base. Streaming (POST /messages) lives in ccStream.ts because it
 * needs a ReadableStream body, not a JSON response.
 */
import type { AnswerElicitBody, TrowelEvent } from "./ccTypes";

const CC_API_BASE = "/api/cc";

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
  /** slice-028 D2: multi-session display name (workdir basename + #N for
   * duplicates) — drives the MultiSessionBar row label. */
  readonly name?: string;
  /** slice-026: whether reverting turns is supported for this workdir (non-git
   * workdirs get the banner + no revert buttons). */
  readonly revert_enabled: boolean;
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

/** slice-028 D2: one row of GET /api/cc/sessions/active — a currently-live
 * trowel session (in-memory registry, distinct from the on-disk history list).
 * The MultiSessionBar renders these and lets the user switch between them. */
export interface ActiveSession {
  /** trowel session id (the registry key, NOT cc's session id). */
  readonly id: string;
  readonly workdir: string;
  readonly model: string;
  /** Display name (basename + #N); falls back to basename if backend omits. */
  readonly name: string;
  /** Whether a turn is mid-stream on this session (send() in flight). */
  readonly running: boolean;
  /** True = 有活 cc 子进程（发过消息且未退出）；temp（从未 spawn）→ false.
   * 前端 reconcile 据此判断是否进多开栏，避免 temp 被误显示成多开。 */
  readonly connected: boolean;
}

/** Result of GET /api/cc/sessions/active — the live sessions + which is active. */
export interface ActiveSessionListResult {
  readonly sessions: readonly ActiveSession[];
  /** The currently-active session id (switch target tracks this). */
  readonly activeId: string | null;
}

/** GET /api/cc/sessions/active — list live trowel sessions + the active id
 * (slice-028 D2). The MultiSessionBar re-fetches after create/close/switch. */
export async function listActiveSessions(): Promise<ActiveSessionListResult> {
  const data = await request<{
    sessions: readonly ActiveSession[];
    active_id: string | null;
  }>(`${CC_API_BASE}/sessions/active`);
  return { sessions: data.sessions, activeId: data.active_id };
}

/** POST /api/cc/sessions/:id/activate — switch the active session without
 * destroying the others (slice-028 D2 multi-session switching). */
export async function activateSession(
  sessionId: string,
): Promise<{ activeId: string }> {
  const data = await request<{ active_id: string }>(
    `${CC_API_BASE}/sessions/${sessionId}/activate`,
    { method: "POST" },
  );
  return { activeId: data.active_id };
}

/** POST /api/cc/sessions/{id}/interrupt — SIGINT the current turn. */
export async function interruptSession(sessionId: string): Promise<void> {
  await request<{ interrupted: boolean }>(
    `${CC_API_BASE}/sessions/${sessionId}/interrupt`,
    { method: "POST" },
  );
}

/** POST /api/cc/sessions/{id}/revert — revert a turn (slice-026 E1).
 * Drops the given turn and every later one: git-restores the worktree to the
 * checkpoint and truncates the CC jsonl; the host then re-resumes CC from the
 * shorter history. */
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

/** POST /api/cc/sessions/{id}/answer — answer (or cancel) a pending
 * AskUserQuestion elicitation (slice-025-c). Returns ok=false when no
 * elicitation was pending (stale-UI race) instead of throwing, so the caller
 * can silently reconcile. Network errors still throw. */
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

/** DELETE /api/cc/sessions/{id} — kill the subprocess and drop the session. */
export async function deleteSession(sessionId: string): Promise<void> {
  await request<{ closed: boolean }>(`${CC_API_BASE}/sessions/${sessionId}`, {
    method: "DELETE",
  });
}

/** One row of GET /api/cc/models — a cc alias and the real model it maps to
 * (slice-027 C2). trowel surfaces cc's own aliases (opus/sonnet/haiku) instead
 * of hardcoding GLM ids, so switching backend only edits settings.json. */
export interface ModelOption {
  readonly value: string;
  readonly label: string;
  readonly real_model: string;
  readonly description: string;
  /** slice-034 feat 3: True for the alias cc falls back to when unset.
   * Optional on the client as a defensive fallback (backend always sends it). */
  readonly is_default?: boolean;
}

/** GET /api/cc/models — alias → real-model mapping for the /model picker. */
export async function listModels(): Promise<readonly ModelOption[]> {
  return request<readonly ModelOption[]>(`${CC_API_BASE}/models`);
}

/** One row of GET /api/cc/slash-items — a '/' autocomplete entry (slice-027 C1).
 * cc init's slash_commands are bare names; descriptions come from reading the
 * skill/command frontmatter locally on the backend. */
export interface SlashItem {
  readonly name: string;
  readonly description: string;
  readonly source: "project" | "user" | "bundled" | "builtin";
  readonly type: "skill" | "command";
}

/** GET /api/cc/slash-items?workdir=... — slash commands + skills for the '/'
 * autocomplete. workdir is required because project .claude/ depends on it. */
export async function listSlashItems(
  workdir: string,
): Promise<readonly SlashItem[]> {
  return request<readonly SlashItem[]>(
    `${CC_API_BASE}/slash-items?workdir=${encodeURIComponent(workdir)}`,
  );
}

/** One row of GET /api/cc/list-dir — an immediate subdirectory (slice-027 C4). */
export interface DirEntry {
  readonly name: string;
  readonly path: string;
}

/** GET /api/cc/list-dir?path=... — immediate subdirs of a path (browser sandbox
 * can't enumerate local dirs). `~` is expanded server-side; files/dotted hidden. */
export async function listDir(path: string): Promise<readonly DirEntry[]> {
  return request<readonly DirEntry[]>(
    `${CC_API_BASE}/list-dir?path=${encodeURIComponent(path)}`,
  );
}

/** URL for POST /messages — passed to ccStream.postMessageStream. */
export function messagesUrl(sessionId: string): string {
  return `${CC_API_BASE}/sessions/${sessionId}/messages`;
}
