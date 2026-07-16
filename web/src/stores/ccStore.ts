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
  activateSession as apiActivateSession,
  createSession as apiCreateSession,
  deleteSession as apiDeleteSession,
  getHistory,
  interruptSession,
  listActiveSessions,
  listSessions,
  messagesUrl,
  revertSession as apiRevertSession,
  type ActiveSession,
  type CcSession,
  type CcSessionSummary,
  type CreateSessionParams,
} from "../api/cc";
import { postMessageStream } from "../api/ccStream";
import type { TrowelEvent } from "../api/ccTypes";

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
}

interface CcState {
  /** All live trowel sessions, keyed by sid. The active one is sessions[activeSid]. */
  readonly sessions: Readonly<Record<string, PerSessionState>>;
  /** The session the message area + composer + todo bar are bound to. */
  readonly activeSid: string | null;
  /** On-disk history for the active session's workdir (the resume dropdown). */
  readonly history: readonly CcSessionSummary[];
  /** Total sessions on disk (meta.total) — true count for "共 N · 最近 M" display. */
  readonly historyTotal: number;
  readonly loadingHistory: boolean;

  // actions
  startSession: (params: CreateSessionParams) => Promise<CcSession | null>;
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
  loadHistoryIntoView: () => Promise<void>;
  send: (text: string) => Promise<void>;
  interrupt: () => Promise<void>;
  /** Submit the user's selections for the pending AskUserQuestion (slice-025-c). */
  answerElicit: (answers: Record<string, string>) => Promise<void>;
  /** Decline the pending AskUserQuestion (writes control_response deny). */
  cancelElicit: () => Promise<void>;
  /** slice-026: revert a turn — drop it and every later turn from the view and
   * ask the backend to git-restore + truncate the jsonl. */
  revertTurn: (turnId: string) => Promise<void>;
  reset: () => void;
}

/** Resource caps mirroring the backend (slice-028 Q5'). */
export const MAX_RUNNING = 5;
export const MAX_CONNECTIONS = 20;

/** Build a fresh CC store. Exported so tests can isolate instances; the app
 * uses the `useCcStore` singleton below. */
export function createCcStore() {
  return create<CcState>((set, get) => {
    /** Route one event to a specific session's reducer. The sid is captured in
     * the send() closure so a stream keeps feeding the session that opened it
     * even after the user switches activeSid mid-stream (Q4 切换不 abort). */
    function applyTo(sid: string, event: TrowelEvent): void {
      set((state) => {
        const cur = state.sessions[sid];
        if (!cur) return state;
        // slice-028 v2: an exited session is dropped entirely (the cc process
        // is gone) — the multi-session bar never shows exited rows, and if it
        // was the active session the view returns to the no-active state.
        // (Per-session reducer still has a session_exited case for completeness,
        // but the live path removes the row here before it ever applies.)
        if (event.type === "session_exited") {
          const sessions = { ...state.sessions };
          delete sessions[sid];
          const activeSid = state.activeSid === sid ? null : state.activeSid;
          return { ...state, sessions, activeSid };
        }
        const reduced = reduceEvent(cur, event);
        let next: PerSessionState = { ...cur, ...reduced };
        // slice-027 C2: effort lives on the shell (createSession params), not
        // ReducerState — fold the /effort change in here.
        if (event.type === "model_changed" && event.effort != null) {
          next = { ...next, effort: event.effort };
        }
        return {
          ...state,
          sessions: { ...state.sessions, [sid]: next },
          activeSid: state.activeSid,
        };
      });
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
        // bar only after the first send spawns cc. Drop any prior temp active
        // first ("切走即丢"). No cap here: caps count connected sessions and are
        // enforced in send().
        await dropTempActive();
        try {
          const session = await apiCreateSession(params);
          const sid = session.session_id;
          const name =
            session.name ?? (params.workdir.split("/").pop() || params.workdir);
          // connected=false until the first send() spawns cc.
          const perSession: PerSessionState = {
            ...INITIAL_REDUCER_STATE,
            workdir: params.workdir,
            effort: params.effort ?? null,
            name,
            revertEnabled: session.revert_enabled,
            transportError: null,
            abort: null,
            connected: false,
          };
          set((state) => ({
            ...state,
            sessions: { ...state.sessions, [sid]: perSession },
            activeSid: sid,
          }));
          return session;
        } catch (err) {
          patchActive(() => ({ transportError: (err as Error).message }));
          return null;
        }
      },

      activateSession: async (sid) => {
        const state = get();
        if (!state.sessions[sid]) return;
        // no-op when clicking the already-active row (also guards dropTempActive
        // from dropping the row the user is activating).
        if (state.activeSid === sid) return;
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
        // dict is empty but the backend _REGISTRY may still hold live cc
        // processes; pull them in as connected rows so the user can see them
        // (and × close / activate). The backend list carries no turns/tasks
        // (those were in-memory) — activating a reconciled row + send continues
        // the session; history replay is via loadHistoryIntoView.
        let backend: readonly ActiveSession[] = [];
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
            if (merged[b.id]) continue; // frontend already tracks it
            merged[b.id] = {
              ...INITIAL_REDUCER_STATE,
              // slice-028 v2 bugfix: 后端 list 带 model，必须写进 meta.model，
              // 否则多开栏 statusText fallback 显示 "model"（被截成 "mdle"）。
              meta: { ...INITIAL_REDUCER_STATE.meta, model: b.model },
              workdir: b.workdir,
              effort: null,
              name: b.name,
              revertEnabled: false, // backend list omits this; re-gained on next send
              transportError: null,
              abort: null,
              // 后端 list 带 connected 字段：True = 有活 cc 进程；temp（从未发消息）→ false。
              // 不再写死 true —— 否则 temp 会被误标 live 显示在多开栏（ClaudeDesktop 多开 bug）。
              connected: b.connected,
            };
          }
          // keep an existing frontend activeSid; else fall back to the backend's
          // active_id, or the first backend session if active_id is null (so a
          // refresh always re-shows SOMETHING when the backend has live sessions).
          const fallback =
            activeId ?? (backend.length > 0 ? backend[0].id : null);
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
          const { sessions, total } = await listSessions(workdir);
          set({ history: sessions, historyTotal: total, loadingHistory: false });
        } catch (err) {
          set({ loadingHistory: false });
          patchActive(() => ({ transportError: (err as Error).message }));
        }
      },

      loadHistoryIntoView: async () => {
        const sid = get().activeSid;
        if (!sid) return;
        const cur = get().sessions[sid];
        if (!cur) return;
        try {
          const events = await getHistory(sid);
          set((state) => {
            const s = state.sessions[sid];
            if (!s) return state;
            let next: ReducerState = { ...INITIAL_REDUCER_STATE, meta: s.meta };
            for (const ev of events) {
              next = reduceEvent(next, ev);
            }
            const finalized = finalizeHistoryForView(next);
            return {
              ...state,
              sessions: { ...state.sessions, [sid]: { ...s, ...finalized } },
            };
          });
        } catch (err) {
          patchActive(() => ({ transportError: (err as Error).message }));
        }
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

// re-export for component convenience
export type { CcSession, CcSessionSummary, CreateSessionParams };
