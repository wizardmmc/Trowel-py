/**
 * ccStore — multi-session zustand shell around the pure ccReducer.
 *
 * Holds `sessions: Record<sid, PerSessionState>` + `activeSid`. Events route to
 * the session that opened the stream (Q4 切换不 abort). MAX_RUNNING /
 * MAX_CONNECTIONS enforce Q5'. The pure reducer (reduceEvent + types + helpers)
 * lives in ccReducer.ts — this file is the transport/effect layer only.
 *
 * slice-028 v2 model: a session is a "connection" (shown in the multi-session
 * bar) only once send() has spawned the cc subprocess (`connected=true`). "+"
 * / load-history states (connected=false) are dropped when switched away
 * ("切走即丢"). refreshActiveSessions reconciles with the backend _REGISTRY
 * after a page refresh so live cc processes aren't orphaned.
 */
import { create } from "zustand";

import {
  answerElicit as apiAnswerElicit,
  revertSession as apiRevertSession,
} from "../api/cc";
import {
  activateAgentSession as apiActivateSession,
  agentMessagesUrl as messagesUrl,
  answerAgentRequest as apiAnswerAgentRequest,
  createAgentSession as apiCreateSession,
  deleteAgentSession as apiDeleteSession,
  getAgentHistory,
  interruptAgentSession as interruptSession,
  listActiveAgentSessions as listActiveSessions,
  listAgentHistory as listSessions,
  listAgentRequests,
  updateAgentSessionSettings as apiUpdateSessionSettings,
  type AgentEventLike,
  type AgentHistoryRow,
  type AgentPendingRequest,
  type AgentSession,
  type Runtime,
} from "../api/agent";
import { agentEventToTrowel, type AgentEvent } from "../api/agentTypes";
import type { ApprovalRequestEvent } from "../api/ccTypes";
import { postMessageStream } from "../api/ccStream";

// Re-export the reducer's full public surface so existing imports from
// "ccStore" (components + tests) keep working unchanged after the split.
export * from "./ccReducer";
import {
  INITIAL_REDUCER_STATE,
  endActiveTurnOnStreamClose,
  finalizeHistoryForView,
  nextTurnId,
  reduceEvent,
  type ReducerState,
  type Turn,
} from "./ccReducer";


// ---------------------------------------------------------------------------
// zustand shell — slice-028 D2 multi-session
// ---------------------------------------------------------------------------

/** Per-session state: the pure ReducerState (turns/phase/meta/tasks) plus the
 * transport + identity fields the shell tracks per session. The store holds a
 * dict of these keyed by trowel sid so switching sessions preserves each one's
 * view (Q4: 切换不 abort, 状态活在前端 store 内存). */
export interface PerSessionState extends ReducerState {
  readonly workdir: string;
  /** Remembered from createSession params (session_started has no effort field). */
  readonly effort: string | null;
  /** Display name from the backend (basename + #N); falls back to basename. */
  readonly name: string;
  /** slice-026: whether reverting turns is supported for this workdir. */
  readonly revertEnabled: boolean;
  /** Transport-level error (fetch failure etc.), per session. */
  readonly transportError: string | null;
  /** Set while a turn is mid-stream (send in flight); cleared in the finally. */
  abort: AbortController | null;
  /** slice-028 v2: true once send() has spawned the cc subprocess. The
   * multi-session bar only shows connected sessions — a "+" / load-history
   * state is `connected=false` (no live cc process yet) and is dropped when
   * the user switches away. Matches the user's mental model: 多开栏 = 有活 cc
   * 进程的 session. */
  readonly connected: boolean;
  /** slice-060: frozen memory/profile A/B switches for this session. Read-only
   * after create (想换条件就新建会话). Reconciled from the backend on refresh. */
  readonly memoryEnabled: boolean;
  readonly profileEnabled: boolean;
  /** slice-072: native runtime of this session (frozen at create, C-1). */
  readonly runtime: Runtime;
  /** slice-072: native session id (cc_session_id / codex thread_id); null
   * until the host reports it, then written back atomically by the Hub. */
  readonly nativeSessionId: string | null;
  /** slice-072: runtime-specific effective permission/policy, null until
   * reported. The multi-session bar shows it as the per-row policy. */
  readonly permission: string | null;
  readonly permissionPreset?: string | null;
  readonly effectivePermissionProfile?: string | null;
  readonly effectiveSandbox?: string | null;
  readonly effectiveApproval?: string | null;
  readonly networkAccess?: boolean | null;
  /** Codex settings selected for the next accepted turn. */
  readonly pendingModel?: string | null;
  readonly pendingEffort?: string | null;
  readonly settingsNotice?: string | null;
  /** slice-072: runtime-declared capability tags — the UI gates features off
   * this list, never off `runtime === ...` (C-6). */
  readonly capabilities: readonly string[];
  /** slice-074: last seq applied to this session (per-session monotonic).
   * Null before the first event. Used to drop dups and detect gaps (spec §3). */
  readonly lastSeq: number | null;
  /** slice-074: a seq gap was observed (event.seq > lastSeq + 1). The durable
   * replay that refills the gap lands in slice-081; this slice only flags it so
   * the UI can show a "needs replay" hint instead of silently losing state. */
  readonly needsReplay: boolean;
}

/** slice-072: startSession params. ``runtime`` defaults to ``claude_code`` so
 * existing CC-only callers are zero-regression. Codex-specific fields
 * (``approval_policy`` / ``sandbox``) are ignored on the CC branch and vice
 * versa (``permission_mode`` is CC-only). */
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

interface CcState {
  /** All live trowel sessions, keyed by sid. The active one is sessions[activeSid]. */
  readonly sessions: Readonly<Record<string, PerSessionState>>;
  /** The session the message area + composer + todo bar are bound to. */
  readonly activeSid: string | null;
  /** On-disk history for the active session's workdir (the resume dropdown). */
  readonly history: readonly AgentHistoryRow[];
  /** Total sessions on disk (meta.total) — true count for "共 N · 最近 M" display. */
  readonly historyTotal: number;
  readonly loadingHistory: boolean;

  // actions
  startSession: (params: StartSessionParams) => Promise<AgentSession>;
  /** slice-028 D2: switch the active session (POST /activate + swap activeSid). */
  activateSession: (sid: string) => Promise<void>;
  /** slice-028 D2: close + drop a session (DELETE + remove from dict). */
  closeSession: (sid: string) => Promise<void>;
  /** slice-028 v2 reload reconcile: pull the backend's live _REGISTRY into the
   * frontend dict so a page refresh doesn't orphan live cc processes. Sessions
   * the backend knows about that the frontend lost (refresh) re-enter the dict
   * as connected rows the user can activate or close. */
  refreshActiveSessions: () => Promise<void>;
  refreshHistory: (workdir: string) => Promise<void>;
  updateSessionSettings: (model: string, effort: string) => Promise<void>;
  loadHistoryIntoView: () => Promise<void>;
  send: (text: string) => Promise<void>;
  interrupt: () => Promise<void>;
  /** Submit the user's selections for the pending AskUserQuestion (slice-025-c). */
  answerElicit: (answers: Record<string, string>) => Promise<void>;
  /** Decline the pending AskUserQuestion (writes control_response deny). */
  cancelElicit: () => Promise<void>;
  /** Answer one pending Codex approval through the host-neutral route. */
  answerApproval: (requestId: string, decision: string) => Promise<void>;
  /** slice-026: revert a turn — drop it and every later turn from the view and
   * ask the backend to git-restore + truncate the jsonl. */
  revertTurn: (turnId: string) => Promise<void>;
  reset: () => void;
}

/** Resource caps mirroring the backend (slice-028 Q5'). */
export const MAX_RUNNING = 5;
export const MAX_CONNECTIONS = 20;

function codexPermissionLabel(
  sandbox: string | null,
  approval: string | null,
): string | null {
  if (sandbox === null && approval === null) return null;
  const labels: Readonly<Record<string, string>> = {
    "read-only": "Read only",
    "workspace-write": "Workspace write",
    "danger-full-access": "Full access",
  };
  return `${labels[sandbox ?? ""] ?? sandbox ?? "Unknown sandbox"} · ${approval ?? "unknown approval"}`;
}

/** Build a fresh CC store. Exported so tests can isolate instances; the app
 * uses the `useCcStore` singleton below. */
export function createCcStore() {
  return create<CcState>((set, get) => {
    /** Route one AgentEvent to a specific session's reducer. The sid is captured
     * in the send() closure so a stream keeps feeding the session that opened it
     * even after the user switches activeSid mid-stream (Q4 切换不 abort).
     *
     * slice-074: the envelope is unwrapped into the flat TrowelEvent shape the
     * reducer consumes (agentEventToTrowel), then seq/gap is enforced before
     * reduceEvent runs. No runtime branching — both runtimes speak the unified
     * TrowelEvent type vocabulary after the backend adapter renamed Codex types. */
    function applyTo(sid: string, event: AgentEvent): void {
      set((state) => {
        const cur = state.sessions[sid];
        if (!cur) return state;
        // slice-074 §3: seq dup → drop. Gap (seq > lastSeq+1) → flag needsReplay
        // (slice-081 refills); the event is still applied best-effort so the UI
        // shows what arrived rather than stalling on the gap.
        if (cur.lastSeq !== null && event.seq <= cur.lastSeq) {
          return state; // duplicate — silently drop
        }
        const gapped =
          cur.lastSeq !== null && event.seq > cur.lastSeq + 1;
        // slice-028 v2 / slice-072: a CC session_exited drops the row (the cc
        // subprocess is gone). Codex host_status(host_exited) does NOT drop the
        // row — the binding survives so the next send can resume (spec §4); the
        // reducer flips the running turn to error instead.
        if (event.type === "session_exited") {
          const sessions = { ...state.sessions };
          delete sessions[sid];
          const activeSid = state.activeSid === sid ? null : state.activeSid;
          return { ...state, sessions, activeSid };
        }
        const flat = agentEventToTrowel(event);
        const reduced = reduceEvent(cur, flat);
        let next: PerSessionState = {
          ...cur,
          ...reduced,
          lastSeq: event.seq,
          needsReplay: cur.needsReplay || gapped,
        };
        // slice-027 C2: effort lives on the shell (createSession params), not
        // ReducerState — fold the /effort change in here (CC only). The flat
        // event carries effort on model_changed.
        const effort = (flat as { effort?: string | null }).effort;
        if (event.type === "model_changed" && effort != null) {
          next = {
            ...next,
            effort,
            pendingModel: null,
            pendingEffort: null,
            settingsNotice: null,
          };
        }
        if (event.type === "session_started" && event.runtime === "codex") {
          const profile = event.payload.permission_profile;
          const sandbox = event.payload.effective_sandbox;
          const approval = event.payload.effective_approval;
          const network = event.payload.network_access;
          const effectiveSandbox = typeof sandbox === "string" ? sandbox : null;
          const effectiveApproval = typeof approval === "string" ? approval : null;
          next = {
            ...next,
            permission: codexPermissionLabel(effectiveSandbox, effectiveApproval),
            effectivePermissionProfile:
              typeof profile === "string" ? profile : null,
            effectiveSandbox,
            effectiveApproval,
            networkAccess: typeof network === "boolean" ? network : null,
          };
        }
        return {
          ...state,
          sessions: { ...state.sessions, [sid]: next },
          activeSid: state.activeSid,
        };
      });
    }

    /** Fold a REST-recovered request into the same reducer as its SSE event. */
    function applyApprovalRequest(
      sid: string,
      request: AgentPendingRequest,
    ): void {
      const event: ApprovalRequestEvent = {
        type: "approval_request",
        turn_id: request.turn_id ?? undefined,
        request_id: request.request_id,
        item_id: request.item_id,
        approval_kind: request.approval_kind,
        command: request.command,
        cwd: request.cwd,
        reason: request.reason,
        available_decisions: request.available_decisions,
        status: request.status,
        decision: request.decision,
        auto_resolved: request.auto_resolved,
        resolution_reason: request.resolution_reason,
      };
      set((state) => {
        const session = state.sessions[sid];
        if (!session) return state;
        const currentTurn = session.turns[session.turns.length - 1];
        const belongsToCurrentTurn =
          request.turn_id === null ||
          currentTurn?.turnId === request.turn_id ||
          currentTurn?.items.some(
            (item) =>
              item.kind === "approval" && item.requestId === request.request_id,
          ) === true;
        const reduced = reduceEvent(session, event);
        return {
          ...state,
          sessions: {
            ...state.sessions,
            [sid]: {
              ...session,
              ...reduced,
              phase: belongsToCurrentTurn ? reduced.phase : session.phase,
            },
          },
        };
      });
    }

    /** Refresh request states without treating a frontend disconnect as reject. */
    async function recoverApprovalRequests(sid: string): Promise<void> {
      const session = get().sessions[sid];
      if (session?.runtime !== "codex") return;
      try {
        const requests = await listAgentRequests(sid);
        for (const request of requests) applyApprovalRequest(sid, request);
      } catch {
        // Recovery is best-effort. The existing inline card remains visible,
        // and the next SSE lifecycle event or explicit answer can still settle it.
      }
    }

    /** Patch the active session (transport errors, etc.). No-op if none active. */
    function patchActive(
      fn: (s: PerSessionState) => Partial<PerSessionState>,
    ): void {
      set((state) => {
        const sid = state.activeSid;
        if (!sid) return state;
        const cur = state.sessions[sid];
        if (!cur) return state;
        return {
          ...state,
          sessions: { ...state.sessions, [sid]: { ...cur, ...fn(cur) } },
        };
      });
    }

    /** slice-028 v2: drop the current active session if it's a "temp" — a
     * never-connected "+" / load-history state (no live cc process). Per the
     * user model, switching away from (or replacing) a temp discards it
     * ("切走即丢"). Best-effort DELETE on the backend; the local row is dropped
     * regardless. Connected / exited / in-turn sessions are kept. */
    async function dropTempActive(): Promise<void> {
      const state = get();
      const sid = state.activeSid;
      if (!sid) return;
      const s = state.sessions[sid];
      if (!s || s.connected || s.meta.exited || s.abort) return;
      try {
        await apiDeleteSession(sid);
      } catch {
        // best-effort: drop the local row anyway
      }
      set((st) => {
        // re-check — the active may have changed during the await
        if (st.activeSid !== sid) return st;
        const sessions = { ...st.sessions };
        delete sessions[sid];
        return { ...st, sessions, activeSid: null };
      });
    }

    return {
      sessions: {},
      activeSid: null,
      history: [],
      historyTotal: 0,
      loadingHistory: false,

      startSession: async (params) => {
        // slice-028 v2: a "+" / load-history / mount creates a session that is
        // NOT yet a connection (connected=false) — it enters the multi-session
        // bar only after the first send spawns the host. Drop any prior temp
        // active first ("切走即丢"). No cap here: caps count connected sessions
        // and are enforced in send().
        await dropTempActive();
        // slice-072: route through the host-neutral /api/agent. runtime
        // defaults to claude_code so pre-existing CC-only callers keep
        // working unchanged (spec C-5). Rejections intentionally propagate to
        // the new-session dialog, which owns the inline error state.
        const runtime: Runtime = params.runtime ?? "claude_code";
        const session = await apiCreateSession({ ...params, runtime });
        const sid = session.session_id;
        const name =
          session.name ?? (params.workdir.split("/").pop() || params.workdir);
        const perSession: PerSessionState = {
          ...INITIAL_REDUCER_STATE,
          meta: {
            ...INITIAL_REDUCER_STATE.meta,
            model: session.model ?? params.model ?? null,
          },
          workdir: params.workdir,
          effort: params.effort ?? null,
          name,
          revertEnabled: session.capabilities.includes("checkpoint"),
          transportError: null,
          abort: null,
          connected: false,
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
        set((state) => ({
          ...state,
          sessions: { ...state.sessions, [sid]: perSession },
          activeSid: sid,
        }));
        return session;
      },

      activateSession: async (sid) => {
        const state = get();
        if (!state.sessions[sid]) return;
        // no-op when clicking the already-active row (also guards dropTempActive
        // from dropping the row the user is activating).
        if (state.activeSid === sid) {
          await recoverApprovalRequests(sid);
          return;
        }
        // slice-028 v2: drop a never-connected temp active when switching away.
        await dropTempActive();
        // Tell the backend this is now active (its _ACTIVE_SID); the frontend
        // dict is the source of truth for view state, so we swap regardless.
        try {
          await apiActivateSession(sid);
        } catch {
          // network error — still swap the local view (best-effort)
        }
        set({ activeSid: sid });
        await recoverApprovalRequests(sid);
      },

      closeSession: async (sid) => {
        const cur = get().sessions[sid];
        if (!cur) return;
        cur.abort?.abort();
        try {
          await apiDeleteSession(sid);
        } catch {
          // best-effort: drop the local row even if DELETE fails
        }
        set((state) => {
          const sessions = { ...state.sessions };
          delete sessions[sid];
          const activeSid = state.activeSid === sid ? null : state.activeSid;
          return { ...state, sessions, activeSid };
        });
      },

      refreshActiveSessions: async () => {
        // slice-028 v2 reload reconcile. After a page refresh the frontend
        // dict is empty but the backend may still hold live sessions; pull
        // them in as connected rows so the user can see them (and × close /
        // activate). The backend list carries no turns/tasks (those were
        // in-memory) — activating a reconciled row + send continues the
        // session; history replay is via loadHistoryIntoView.
        // slice-072: the list is now the host-neutral /api/agent active list
        // (mixed CC + Codex); each row carries runtime/native/permission/
        // capabilities so the multi-session bar renders both runtimes.
        let backend: readonly AgentSession[] = [];
        let activeId: string | null = null;
        try {
          const result = await listActiveSessions();
          backend = result.sessions;
          activeId = result.activeId;
        } catch {
          // backend unreachable → nothing to reconcile
          return;
        }
        set((state) => {
          const merged = { ...state.sessions };
          for (const b of backend) {
            if (merged[b.session_id]) continue; // frontend already tracks it
            merged[b.session_id] = {
              ...INITIAL_REDUCER_STATE,
              // 后端 list 带 model，必须写进 meta.model，否则多开栏 fallback
              // 显示 "model"（被截成 "mdle"）。
              meta: { ...INITIAL_REDUCER_STATE.meta, model: b.model },
              workdir: b.workdir,
              effort: b.effort,
              name: b.name,
              revertEnabled: b.capabilities.includes("checkpoint"),
              transportError: null,
              abort: null,
              connected: b.connected,
              memoryEnabled: b.memory_enabled,
              profileEnabled: b.profile_enabled,
              runtime: b.runtime,
              nativeSessionId: b.native_session_id,
              permission: b.permission,
              permissionPreset: b.permission_preset,
              effectivePermissionProfile: b.effective_permission_profile,
              effectiveSandbox: b.effective_sandbox,
              effectiveApproval: b.effective_approval,
              networkAccess: b.network_access,
              pendingModel: null,
              pendingEffort: null,
              settingsNotice: null,
              capabilities: b.capabilities,
              lastSeq: null,
              needsReplay: false,
            };
          }
          // keep an existing frontend activeSid; else fall back to the backend's
          // active_id, or the first backend session if active_id is null (so a
          // refresh always re-shows SOMETHING when the backend has live sessions).
          const fallback =
            activeId ?? (backend.length > 0 ? backend[0].session_id : null);
          const activeSid =
            state.activeSid && merged[state.activeSid]
              ? state.activeSid
              : fallback && merged[fallback]
                ? fallback
                : state.activeSid;
          return { ...state, sessions: merged, activeSid };
        });
      },

      refreshHistory: async (workdir) => {
        set({ loadingHistory: true });
        try {
          // slice-072: mixed history (CC jsonl rows + Codex bindings) via
          // /api/agent. The agent endpoint returns a flat list with no
          // meta.total; the switcher shows the list length as the count.
          const rows = await listSessions(workdir);
          set({
            history: rows,
            historyTotal: rows.length,
            loadingHistory: false,
          });
        } catch (err) {
          set({ loadingHistory: false });
          patchActive(() => ({ transportError: (err as Error).message }));
        }
      },

      updateSessionSettings: async (model, effort) => {
        const sid = get().activeSid;
        if (!sid) return;
        const current = get().sessions[sid];
        if (!current || current.runtime !== "codex" || current.abort) return;
        try {
          const selection = await apiUpdateSessionSettings(sid, { model, effort });
          set((state) => {
            const session = state.sessions[sid];
            if (!session) return state;
            return {
              ...state,
              sessions: {
                ...state.sessions,
                [sid]: {
                  ...session,
                  pendingModel: selection.model,
                  pendingEffort: selection.effort,
                  settingsNotice: selection.adjusted
                    ? `当前模型不支持所选 effort，已改为 ${selection.effort}`
                    : "将在下一轮生效",
                  transportError: null,
                },
              },
            };
          });
        } catch (err) {
          patchActive(() => ({ transportError: (err as Error).message }));
        }
      },

      loadHistoryIntoView: async () => {
        const sid = get().activeSid;
        if (!sid) return;
        const cur = get().sessions[sid];
        if (!cur) return;
        // slice-074: history replay now flows through the unified
        // /api/agent/sessions/{id}/history endpoint (AgentEvent v1 envelopes).
        // CC wraps cc_host's jsonl scan; Codex returns 501 until slice-079,
        // so a non-200 just leaves the view as-is (no transport error — the
        // user simply cannot replay a Codex thread yet).
        if (cur.runtime !== "claude_code") return;
        let envelopes: readonly AgentEventLike[] = [];
        try {
          envelopes = await getAgentHistory(sid);
        } catch {
          // 501 (Codex, slice-079) or network error — leave the view unchanged.
          return;
        }
        set((state) => {
          const s = state.sessions[sid];
          if (!s) return state;
          let next: ReducerState = { ...INITIAL_REDUCER_STATE, meta: s.meta };
          // slice-074: history is an INDEPENDENT seq namespace (the backend
          // history adapter starts seq at 1). Track it locally just to drop dups
          // within the replay, then RESET the live watermark to null so the next
          // live stream re-establishes seq from its own first event — otherwise
          // live seq 1..N would be dropped as dups of the history seq 1..N
          // (claude C-2 / codex HIGH review finding).
          let replaySeq: number | null = null;
          for (const ev of envelopes) {
            if (replaySeq !== null && ev.seq <= replaySeq) continue;
            replaySeq = ev.seq;
            next = reduceEvent(next, agentEventToTrowel(ev as AgentEvent));
          }
          const finalized = finalizeHistoryForView(next);
          return {
            ...state,
            sessions: {
              ...state.sessions,
              [sid]: { ...s, ...finalized, lastSeq: null, needsReplay: false },
            },
          };
        });
      },

      send: async (text) => {
        const sid = get().activeSid;
        if (!sid) {
          return;
        }
        // optimistic turn + cap checks all happen inside ONE set callback so the
        // same-session guard, the MAX_RUNNING cap, and the abort write are
        // atomic (zustand serializes set callbacks; a get()–then–set() split
        // would let two concurrent sends both pass the cap check before either
        // wrote its abort — violating Q5' 在跑 ≤ 5).
        const turn: Turn = {
          id: nextTurnId(),
          userText: text,
          items: [],
          status: "active",
          turnId: null,
          revertible: false,
          // Stamp the live turn's start so the finished event can compute this
          // turn's wall-clock duration ("Ran for Ns"). History-replayed turns
          // never go through send() — they get durationSeconds from UserEvent.
          startedAtMs: Date.now(),
        };
        const abort = new AbortController();
        let accepted = true;
        set((state) => {
          const s = state.sessions[sid];
          if (!s) {
            accepted = false;
            return state;
          }
          // Refuse a second concurrent send INTO THE SAME SESSION (two streams
          // into one reducer would interleave deltas). Other sessions may stream
          // concurrently — that's the multi-session point (Q4).
          if (s.abort) {
            accepted = false;
            return state;
          }
          // slice-028 Q5': at most MAX_RUNNING sessions streaming at once.
          const running = Object.values(state.sessions).filter(
            (x) => x.abort !== null,
          ).length;
          if (running >= MAX_RUNNING) {
            accepted = false;
            return {
              ...state,
              sessions: {
                ...state.sessions,
                [sid]: {
                  ...s,
                  transportError: `同时 in-turn 的 session 已达上限（${MAX_RUNNING}），等一个完成或中断`,
                },
              },
            };
          }
          // slice-028 v2 Q5': at most MAX_CONNECTIONS connected sessions. A
          // send on a !connected session is what connects it (spawns cc), so
          // the cap fires here — a "+" / load-history temp doesn't count until
          // the user actually sends. Re-sends on an already-connected session
          // don't re-count.
          if (!s.connected) {
            const connectedCount = Object.values(state.sessions).filter(
              (x) => x.connected && !x.meta.exited,
            ).length;
            if (connectedCount >= MAX_CONNECTIONS) {
              accepted = false;
              return {
                ...state,
                sessions: {
                  ...state.sessions,
                  [sid]: {
                    ...s,
                    transportError: `连接数已达上限（${MAX_CONNECTIONS}），请先关闭一些 session`,
                  },
                },
              };
            }
          }
          return {
            ...state,
            sessions: {
              ...state.sessions,
              [sid]: {
                ...s,
                turns: [...s.turns, turn],
                phase: "awaiting_first",
                transportError: null,
                abort,
                // slice-028 v2: this send spawns the cc subprocess → the
                // session is now a live connection (enters the multi-session
                // bar, counts toward the connection cap).
                connected: true,
              },
            },
          };
        });
        if (!accepted) return;

        let transportOk = false;
        try {
          await postMessageStream(
            messagesUrl(sid),
            { text },
            (ev) => applyTo(sid, ev),
            { signal: abort.signal },
          );
          transportOk = true;
        } catch (err) {
          set((state) => {
            const s = state.sessions[sid];
            if (!s) return state;
            return {
              ...state,
              sessions: {
                ...state.sessions,
                [sid]: { ...s, transportError: (err as Error).message },
              },
            };
          });
          if (!abort.signal.aborted) await recoverApprovalRequests(sid);
        } finally {
          // Slash commands end the stream without a finished (see
          // endActiveTurnOnStreamClose); a clean close on an active turn must
          // still re-enable the composer.
          set((state) => {
            const s = state.sessions[sid];
            if (!s) return state;
            const closed = endActiveTurnOnStreamClose(s, {
              aborted: abort.signal.aborted,
              transportOk,
            });
            return {
              ...state,
              sessions: {
                ...state.sessions,
                [sid]: { ...s, ...closed, abort: null },
              },
            };
          });
        }
      },

      interrupt: async () => {
        const sid = get().activeSid;
        if (!sid) return;
        const cur = get().sessions[sid];
        cur?.abort?.abort();
        try {
          await interruptSession(sid);
        } catch (err) {
          patchActive(() => ({ transportError: (err as Error).message }));
        }
      },

      answerElicit: async (answers) => {
        const sid = get().activeSid;
        if (!sid) return;
        try {
          await apiAnswerElicit(sid, { answers, cancel: false });
        } catch (err) {
          patchActive(() => ({ transportError: (err as Error).message }));
        }
      },

      cancelElicit: async () => {
        const sid = get().activeSid;
        if (!sid) return;
        try {
          await apiAnswerElicit(sid, { answers: {}, cancel: true });
        } catch (err) {
          patchActive(() => ({ transportError: (err as Error).message }));
        }
      },

      answerApproval: async (requestId, decision) => {
        const sid = get().activeSid;
        if (!sid) return;
        const session = get().sessions[sid];
        if (session?.runtime !== "codex") return;
        try {
          const result = await apiAnswerAgentRequest(sid, requestId, decision);
          applyApprovalRequest(sid, result.request);
        } catch (err) {
          set((state) => {
            const current = state.sessions[sid];
            if (!current) return state;
            return {
              ...state,
              sessions: {
                ...state.sessions,
                [sid]: { ...current, transportError: (err as Error).message },
              },
            };
          });
        }
      },

      revertTurn: async (turnId) => {
        const sid = get().activeSid;
        if (!sid) return;
        const cur = get().sessions[sid];
        // Refuse while a turn is mid-stream — reverting under a live CC process
        // would race its writes. The UI also disables the button while streaming.
        if (!cur || cur.abort) return;
        try {
          await apiRevertSession(sid, turnId);
          // Drop the reverted turn and every later turn from the view. The
          // backend already truncated the jsonl + git-restored the worktree and
          // will re-resume CC from the shorter history on the next send.
          set((state) => {
            const s = state.sessions[sid];
            if (!s) return state;
            const idx = s.turns.findIndex((t) => t.turnId === turnId);
            if (idx === -1) return state;
            const turns = s.turns.slice(0, idx);
            return {
              ...state,
              sessions: {
                ...state.sessions,
                [sid]: {
                  ...s,
                  turns,
                  phase: turns.length === 0 ? "idle" : "done",
                },
              },
            };
          });
        } catch (err) {
          patchActive(() => ({ transportError: (err as Error).message }));
        }
      },

      reset: () => {
        // abort every live stream, then drop all sessions
        for (const s of Object.values(get().sessions)) {
          s.abort?.abort();
        }
        set({
          sessions: {},
          activeSid: null,
          history: [],
          historyTotal: 0,
          loadingHistory: false,
        });
      },
    };
  });
}

export const useCcStore = createCcStore();

/** Selector: the active session's full state (or null when none active).
 * Components read fields off this. Background sessions' events don't trigger
 * rerenders here because the active session's PerSessionState ref only changes
 * when *it* is updated (sessions[activeSid] is stable across other sessions'
 * reducer applies). */
export function useActiveSession(): PerSessionState | null {
  return useCcStore((s) => (s.activeSid ? s.sessions[s.activeSid] ?? null : null));
}

// re-export for component convenience (StartSessionParams is exported above).
export type { AgentHistoryRow, AgentSession, Runtime };
