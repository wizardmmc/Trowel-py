/**
 * REST client for /api/agent — the host-neutral Session Hub (slice-072).
 *
 * Mirrors trowel_py/agent_host/routes.py 1:1. Two runtimes (claude_code /
 * codex) live behind one endpoint family; the frontend never guesses runtime
 * from model or UI state — it reads it off the AgentSession binding (spec
 * C-3). Streaming (POST /messages) lives in ccStream.ts via
 * :func:`agentMessagesUrl` — the SSE frames are AgentEvent v1 envelopes; the
 * store unwraps them (agentEventToTrowel) and feeds ONE reducer (ccReducer),
 * since slice-074 unified both runtimes onto the TrowelEvent type vocabulary.
 *
 * Legacy /api/cc/* stays untouched (spec C-5); the CC history-replay path
 * (GET /api/cc/sessions/{id}/history) is still used for CC sessions created
 * via /api/agent, because they land in the same live _REGISTRY.
 */

export type Runtime = "claude_code" | "codex";

/** One row of GET /api/agent/sessions/{id} / POST /api/agent/sessions. */
export interface AgentSession {
  readonly session_id: string;
  readonly runtime: Runtime;
  /** cc_session_id / Codex thread_id, or null for a fresh session. */
  readonly native_session_id: string | null;
  readonly workdir: string;
  /** Effective model once the host reports one; else null. */
  readonly model: string | null;
  readonly effort: string | null;
  /** Runtime-specific effective policy string; null until reported. */
  readonly permission: string | null;
  readonly memory_enabled: boolean;
  readonly profile_enabled: boolean;
  /** Runtime-declared capability tags (tools/approval/checkpoint/...). */
  readonly capabilities: readonly string[];
  /** Display name (workdir basename + #N). */
  readonly name: string;
  readonly connected: boolean;
  readonly running: boolean;
}

/** Body for POST /api/agent/sessions. runtime is required at the wire level. */
export interface CreateAgentSessionParams {
  readonly runtime: Runtime;
  readonly workdir: string;
  readonly resume_from?: string;
  readonly model?: string;
  readonly effort?: string;
  /** CC only (--permission-mode). */
  readonly permission_mode?: string;
  /** Codex only (approvalPolicy). */
  readonly approval_policy?: string;
  /** Codex only (sandbox mode). */
  readonly sandbox?: string;
  readonly memory_enabled?: boolean;
  readonly profile_enabled?: boolean;
}

/** One row of GET /api/agent/sessions?workdir (mixed history). */
export interface AgentHistoryRow {
  readonly runtime: Runtime;
  readonly native_session_id: string | null;
  readonly title: string;
  /** CC rows: epoch seconds (float); Codex rows: ISO timestamp string. */
  readonly updated_at: number | string;
}

/** One entry of GET /api/agent/runtimes. */
export interface AgentRuntimeInfo {
  readonly runtime: Runtime;
  readonly label: string;
  readonly native: string;
  readonly capabilities: readonly string[];
  readonly connected: boolean;
}

const AGENT_API_BASE = "/api/agent";

interface ApiEnvelope<T> {
  readonly success: boolean;
  readonly data: T | null;
  readonly error: string | null;
}

async function request<T>(url: string, options?: RequestInit): Promise<T> {
  const response = await fetch(url, options);
  if (!response.ok) {
    throw new Error(`Agent API error: ${response.status}`);
  }
  const result: ApiEnvelope<T> = await response.json();
  if (!result.success || result.error) {
    throw new Error(result.error ?? "Agent API call failed");
  }
  return result.data as T;
}

/** POST /api/agent/sessions — create a session under the chosen runtime. */
export async function createAgentSession(
  params: CreateAgentSessionParams,
): Promise<AgentSession> {
  return request<AgentSession>(`${AGENT_API_BASE}/sessions`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(params),
  });
}

/** Result of GET /api/agent/sessions/active — the live mixed list + active id. */
export interface ActiveAgentListResult {
  readonly sessions: readonly AgentSession[];
  readonly activeId: string | null;
}

/** GET /api/agent/sessions/active — list live sessions (CC + Codex) + active. */
export async function listActiveAgentSessions(): Promise<ActiveAgentListResult> {
  const data = await request<{
    sessions: readonly AgentSession[];
    active_id: string | null;
  }>(`${AGENT_API_BASE}/sessions/active`);
  return { sessions: data.sessions, activeId: data.active_id };
}

/** POST /api/agent/sessions/{id}/activate — switch the active (view) session. */
export async function activateAgentSession(
  sessionId: string,
): Promise<{ activeId: string }> {
  const data = await request<{ active_id: string }>(
    `${AGENT_API_BASE}/sessions/${sessionId}/activate`,
    { method: "POST" },
  );
  return { activeId: data.active_id };
}

/** GET /api/agent/sessions/{id} — one session's binding. */
export async function getAgentSession(sessionId: string): Promise<AgentSession> {
  return request<AgentSession>(`${AGENT_API_BASE}/sessions/${sessionId}`);
}

/** DELETE /api/agent/sessions/{id} — close the host + drop the binding. */
export async function deleteAgentSession(
  sessionId: string,
): Promise<{ closed: boolean }> {
  return request<{ closed: boolean }>(`${AGENT_API_BASE}/sessions/${sessionId}`, {
    method: "DELETE",
  });
}

/** POST /api/agent/sessions/{id}/interrupt — interrupt the running turn. */
export async function interruptAgentSession(
  sessionId: string,
): Promise<{ interrupted: boolean }> {
  return request<{ interrupted: boolean }>(
    `${AGENT_API_BASE}/sessions/${sessionId}/interrupt`,
    { method: "POST" },
  );
}

/** GET /api/agent/runtimes — the two runtimes + capabilities + connection. */
export async function listAgentRuntimes(): Promise<readonly AgentRuntimeInfo[]> {
  return request<readonly AgentRuntimeInfo[]>(`${AGENT_API_BASE}/runtimes`);
}

/** GET /api/agent/sessions?workdir=... — mixed history (CC jsonl + Codex). */
export async function listAgentHistory(
  workdir: string,
): Promise<readonly AgentHistoryRow[]> {
  return request<readonly AgentHistoryRow[]>(
    `${AGENT_API_BASE}/sessions?workdir=${encodeURIComponent(workdir)}`,
  );
}

/** URL for POST /messages — passed to ccStream.postMessageStream. */
export function agentMessagesUrl(sessionId: string): string {
  return `${AGENT_API_BASE}/sessions/${sessionId}/messages`;
}

/**
 * GET /api/agent/sessions/{id}/history — replay a session's stored events as
 * AgentEvent v1 envelopes (slice-074). CC history wraps cc_host's on-disk jsonl
 * scan through the CC adapter; Codex returns 501 until slice-079 wires
 * thread/items/list. The frontend unwraps each envelope and feeds the same
 * reducer the live path uses (spec C-2: live/history same reducer).
 */
export async function getAgentHistory(
  sessionId: string,
): Promise<readonly AgentEventLike[]> {
  return request<readonly AgentEventLike[]>(
    `${AGENT_API_BASE}/sessions/${sessionId}/history`,
  );
}

/** Minimal envelope shape getAgentHistory returns (avoid a circular import of
 * the full AgentEvent type from agentTypes.ts at module load). */
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
