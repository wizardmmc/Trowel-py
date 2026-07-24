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
import type { AgentEvent } from "../api/agentTypes";
import { postMessageStream } from "../api/ccStream";

export * from "./ccReducer";
import {
  endActiveTurnOnStreamClose,
  nextTurnId,
  type Turn,
} from "./ccReducer";
import {
  createNewSessionState,
  createReconciledSessionState,
  type PerSessionState,
  type StartSessionParams,
} from "./ccStore/sessionState";
import { applyPendingApproval } from "./ccStore/approvalState";
import { reduceAgentEvent } from "./ccStore/eventState";
import { replayAgentHistory } from "./ccStore/historyState";
import { admitSessionSend } from "./ccStore/sendAdmission";

export type {
  PerSessionState,
  StartSessionParams,
} from "./ccStore/sessionState";
export { MAX_CONNECTIONS, MAX_RUNNING } from "./ccStore/sendAdmission";

/** 管理多会话字典与 transport；事件状态变化统一交给纯 reducer。 */
interface CcState {
  readonly sessions: Readonly<Record<string, PerSessionState>>;
  readonly activeSid: string | null;
  readonly history: readonly AgentHistoryRow[];
  /** 磁盘中的真实总数，用于显示“共 N · 最近 M”。 */
  readonly historyTotal: number;
  readonly loadingHistory: boolean;
  readonly loadingMoreHistory: boolean;
  readonly historyCursor: string | null;
  readonly historyHasMore: boolean;
  readonly historyWorkdir: string | null;
  readonly historyError: string | null;

  startSession: (params: StartSessionParams) => Promise<AgentSession>;
  activateSession: (sid: string) => Promise<void>;
  closeSession: (sid: string) => Promise<void>;
  refreshActiveSessions: () => Promise<void>;
  refreshHistory: (workdir: string) => Promise<void>;
  loadMoreHistory: () => Promise<void>;
  updateSessionSettings: (model: string, effort: string) => Promise<void>;
  loadHistoryIntoView: () => Promise<void>;
  send: (text: string) => Promise<void>;
  interrupt: () => Promise<void>;
  answerElicit: (answers: Record<string, string>) => Promise<void>;
  cancelElicit: () => Promise<void>;
  answerApproval: (requestId: string, decision: string) => Promise<void>;
  revertTurn: (turnId: string) => Promise<void>;
  reset: () => void;
}

export function createCcStore() {
  return create<CcState>((set, get) => {
    let historyGeneration = 0;
    let historyLoadMorePromise: Promise<void> | null = null;
    let historyLoadMoreToken: symbol | null = null;
    let sessionStartGeneration = 0;

    function applyTo(sid: string, event: AgentEvent): void {
      set((state) => {
        const cur = state.sessions[sid];
        if (!cur) return state;
        const result = reduceAgentEvent(cur, event);
        if (result.kind === "duplicate") return state;
        if (result.kind === "session_exited") {
          const sessions = { ...state.sessions };
          delete sessions[sid];
          const activeSid = state.activeSid === sid ? null : state.activeSid;
          return { ...state, sessions, activeSid };
        }
        return {
          ...state,
          sessions: { ...state.sessions, [sid]: result.session },
          activeSid: state.activeSid,
        };
      });
    }

    function applyApprovalRequest(
      sid: string,
      request: AgentPendingRequest,
    ): void {
      set((state) => {
        const session = state.sessions[sid];
        if (!session) return state;
        return {
          ...state,
          sessions: {
            ...state.sessions,
            [sid]: applyPendingApproval(session, request),
          },
        };
      });
    }

    async function recoverApprovalRequests(sid: string): Promise<void> {
      const session = get().sessions[sid];
      if (session?.runtime !== "codex") return;
      try {
        const requests = await listAgentRequests(sid);
        for (const request of requests) applyApprovalRequest(sid, request);
      } catch {
        // 恢复失败不清除现有请求，等待下一次 SSE 或显式重试。
      }
    }

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

    async function dropTempActive(): Promise<void> {
      const state = get();
      const sid = state.activeSid;
      if (!sid) return;
      const s = state.sessions[sid];
      if (!s || s.connected || s.meta.exited || s.abort) return;
      try {
        await apiDeleteSession(sid);
      } catch {
        // 后端删除失败也要丢弃本地临时行。
      }
      set((st) => {
        // await 期间活动会话可能已切换，不能删除新会话。
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
      loadingMoreHistory: false,
      historyCursor: null,
      historyHasMore: false,
      historyWorkdir: null,
      historyError: null,

      startSession: async (params) => {
        const generation = ++sessionStartGeneration;
        // 未连接的临时会话不计入并发上限，切换前直接丢弃。
        await dropTempActive();
        const runtime: Runtime = params.runtime ?? "claude_code";
        const session = await apiCreateSession({ ...params, runtime });
        const sid = session.session_id;
        if (generation !== sessionStartGeneration) {
          try {
            await apiDeleteSession(sid);
          } catch {
            // 迟到请求不能重新占据界面；后端清理保持 best-effort。
          }
          return session;
        }
        const perSession = createNewSessionState(session, params);
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
        if (state.activeSid === sid) {
          await recoverApprovalRequests(sid);
          return;
        }
        await dropTempActive();
        try {
          await apiActivateSession(sid);
        } catch {
          // 后端激活失败仍允许本地切换视图。
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
          // 关闭操作以本地状态为准，后端删除是 best-effort。
        }
        set((state) => {
          const sessions = { ...state.sessions };
          delete sessions[sid];
          const activeSid = state.activeSid === sid ? null : state.activeSid;
          return { ...state, sessions, activeSid };
        });
      },

      refreshActiveSessions: async () => {
        let backend: readonly AgentSession[] = [];
        let activeId: string | null = null;
        try {
          const result = await listActiveSessions();
          backend = result.sessions;
          activeId = result.activeId;
        } catch {
          return;
        }
        set((state) => {
          const merged = { ...state.sessions };
          for (const b of backend) {
            if (merged[b.session_id]) continue;
            merged[b.session_id] = createReconciledSessionState(b);
          }
          const activeSid =
            state.activeSid && merged[state.activeSid]
              ? state.activeSid
              : activeId && merged[activeId]
                ? activeId
                : state.activeSid;
          return { ...state, sessions: merged, activeSid };
        });
      },

      refreshHistory: async (workdir) => {
        const generation = ++historyGeneration;
        historyLoadMorePromise = null;
        historyLoadMoreToken = null;
        set((state) => {
          const changedWorkdir = state.historyWorkdir !== workdir;
          return {
            loadingHistory: true,
            loadingMoreHistory: false,
            history: changedWorkdir ? [] : state.history,
            historyTotal: changedWorkdir ? 0 : state.historyTotal,
            historyWorkdir: workdir,
            historyCursor: null,
            historyHasMore: false,
            historyError: null,
          };
        });
        try {
          const page = await listSessions(workdir, { limit: 20 });
          if (generation !== historyGeneration) return;
          set({
            history: page.rows,
            historyTotal: page.rows.length,
            loadingHistory: false,
            historyCursor: page.nextCursor,
            historyHasMore: page.nextCursor !== null,
          });
        } catch (err) {
          if (generation !== historyGeneration) return;
          set({
            loadingHistory: false,
            historyError: (err as Error).message,
          });
          patchActive(() => ({ transportError: (err as Error).message }));
        }
      },

      loadMoreHistory: () => {
        if (historyLoadMorePromise) return historyLoadMorePromise;
        const snapshot = get();
        if (
          !snapshot.historyHasMore ||
          !snapshot.historyCursor ||
          !snapshot.historyWorkdir
        ) {
          return Promise.resolve();
        }
        const generation = historyGeneration;
        const cursor = snapshot.historyCursor;
        const workdir = snapshot.historyWorkdir;
        const token = Symbol("history-load-more");
        historyLoadMoreToken = token;
        set({ loadingMoreHistory: true, historyError: null });
        const pending = (async () => {
          try {
            const page = await listSessions(workdir, { limit: 20, cursor });
            if (generation !== historyGeneration) return;
            set((state) => {
              if (
                state.historyWorkdir !== workdir ||
                state.historyCursor !== cursor
              ) {
                return state;
              }
              const seen = new Set(
                state.history.map(
                  (row) => `${row.runtime}:${row.native_session_id ?? ""}`,
                ),
              );
              const added = page.rows.filter((row) => {
                const key = `${row.runtime}:${row.native_session_id ?? ""}`;
                if (seen.has(key)) return false;
                seen.add(key);
                return true;
              });
              const history = [...state.history, ...added];
              return {
                ...state,
                history,
                historyTotal: history.length,
                historyCursor: page.nextCursor,
                historyHasMore: page.nextCursor !== null,
                loadingMoreHistory: false,
              };
            });
          } catch (err) {
            if (generation !== historyGeneration) return;
            set({
              loadingMoreHistory: false,
              historyError: (err as Error).message,
            });
          } finally {
            if (historyLoadMoreToken === token) {
              historyLoadMorePromise = null;
              historyLoadMoreToken = null;
            }
          }
        })();
        historyLoadMorePromise = pending;
        return historyLoadMorePromise;
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
        let envelopes: readonly AgentEventLike[] = [];
        try {
          envelopes = await getAgentHistory(sid);
        } catch {
          // 不支持回放或网络失败时保留当前视图。
          return;
        }
        set((state) => {
          const s = state.sessions[sid];
          if (!s) return state;
          return {
            ...state,
            sessions: {
              ...state.sessions,
              [sid]: replayAgentHistory(s, envelopes),
            },
          };
        });
      },

      send: async (text) => {
        const sid = get().activeSid;
        if (!sid) {
          return;
        }
        // 上限检查与 abort 写入必须在同一个 set 回调中原子完成。
        const turn: Turn = {
          id: nextTurnId(),
          userText: text,
          items: [],
          status: "active",
          turnId: null,
          revertible: false,
          startedAtMs: Date.now(),
        };
        const abort = new AbortController();
        let accepted = true;
        set((state) => {
          const admission = admitSessionSend(
            state.sessions,
            sid,
            turn,
            abort,
          );
          accepted = admission.accepted;
          if (admission.sessions === state.sessions) return state;
          return { ...state, sessions: admission.sessions };
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
        // 流式写入期间回退会与后端落盘竞争。
        if (!cur || cur.abort) return;
        try {
          await apiRevertSession(sid, turnId);
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
        sessionStartGeneration += 1;
        for (const s of Object.values(get().sessions)) {
          s.abort?.abort();
        }
        set({
          sessions: {},
          activeSid: null,
          history: [],
          historyTotal: 0,
          loadingHistory: false,
          loadingMoreHistory: false,
          historyCursor: null,
          historyHasMore: false,
          historyWorkdir: null,
          historyError: null,
        });
      },
    };
  });
}

export const useCcStore = createCcStore();

/** 只订阅活动会话，后台会话更新不会触发中心区域重渲染。 */
export function useActiveSession(): PerSessionState | null {
  return useCcStore((s) => (s.activeSid ? s.sessions[s.activeSid] ?? null : null));
}

export type { AgentHistoryRow, AgentSession, Runtime };
