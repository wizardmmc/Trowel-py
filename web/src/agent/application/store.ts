/** 管理多会话字典、Agent 命令、连接生命周期和 transport 调用。 */

import { create } from "zustand";

import {
  answerElicit as apiAnswerElicit,
  revertSession as apiRevertSession,
} from "../../api/cc";
import {
  answerAgentRequest as apiAnswerAgentRequest,
  createAgentSession as apiCreateSession,
  clearCodexGoal as apiClearCodexGoal,
  compactCodexSession as apiCompactCodexSession,
  deleteAgentSession as apiDeleteSession,
  getAgentHistory,
  generateAgentSessionTitle as apiGenerateSessionTitle,
  getCodexSubagentHistory,
  interruptAgentSession as interruptSession,
  listActiveAgentSessions as listActiveSessions,
  listAgentHistory as listSessions,
  listAgentRequests,
  renameAgentSessionTitle as apiRenameSessionTitle,
  setCodexGoal as apiSetCodexGoal,
  startCodexReview as apiStartCodexReview,
  startAgentTurn as apiStartAgentTurn,
  updateAgentSessionSettings as apiUpdateSessionSettings,
  updateAgentPermissionPreset as apiUpdatePermissionPreset,
  type AgentEventLike,
  type PermissionPreset,
  type AgentHistoryRow,
  type AgentPendingRequest,
  type AgentSession,
  type SetCodexGoalInput,
  type CodexReviewTarget,
  type Runtime,
} from "../transport/api";
import type { AgentEvent } from "../transport/agentEvent";
import {
  agentProblemFromUnknown,
  type AgentTransportProblem,
} from "../transport/httpError";

import {
  declinePendingElicitation,
  nextTurnId,
  reduceEvent,
  type Turn,
} from "../domain/reducer";
import {
  CLEARED_TRANSPORT_ISSUE,
  createNewSessionState,
  createReconciledSessionState,
  isRootTurnInFlight,
  materializeCurrentRootTurn,
  promptSessionTitle,
  reconcileCurrentRootTurn,
  transportIssueFromMessage,
  transportIssueFromProblem,
  type PerSessionState,
  type StartSessionParams,
} from "./store/sessionState";
import { applyPendingApproval } from "./store/approvalState";
import { reduceAgentEvent } from "./store/eventState";
import { replayAgentHistory } from "./store/historyState";
import { admitSessionSend } from "./store/sendAdmission";
import { createAgentLiveController } from "./store/agentLive";
import { replayCodexSubagentHistory } from "./store/codexSubagents";

export type { PerSessionState, StartSessionParams } from "./store/sessionState";
export { MAX_CONNECTIONS, MAX_RUNNING } from "./store/sendAdmission";

/** 管理多会话字典与 transport；事件状态变化统一交给纯 reducer。 */
export interface AgentState {
  readonly sessions: Readonly<Record<string, PerSessionState>>;
  readonly closingSessionIds: ReadonlySet<string>;
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
  showWorkspaceHome: () => void;
  closeSession: (sid: string) => Promise<void>;
  refreshActiveSessions: () => Promise<void>;
  refreshHistory: (workdir: string) => Promise<void>;
  loadMoreHistory: () => Promise<void>;
  updateSessionSettings: (model: string, effort: string) => Promise<void>;
  selectSessionPermissionPreset: (preset: PermissionPreset) => Promise<void>;
  loadHistoryIntoView: () => Promise<void>;
  loadCodexSubagentHistory: (threadId: string) => Promise<void>;
  renameSessionTitle: (sid: string, title: string) => Promise<void>;
  send: (text: string) => Promise<void>;
  interrupt: () => Promise<void>;
  answerElicit: (answers: Record<string, string>) => Promise<void>;
  cancelElicit: () => Promise<void>;
  answerApproval: (requestId: string, decision: string) => Promise<void>;
  setCodexGoal: (update: SetCodexGoalInput) => Promise<void>;
  clearCodexGoal: () => Promise<void>;
  compactCodex: () => Promise<void>;
  startCodexReview: (target: CodexReviewTarget) => Promise<void>;
  revertTurn: (turnId: string) => Promise<void>;
  reset: () => void;
}

interface AgentStoreOptions {
  /** 仅用于缩短测试中的实时连接 readiness 预算。 */
  readonly liveReadinessTimeoutMs?: number;
}

export function createAgentStore(options: AgentStoreOptions = {}) {
  return create<AgentState>((set, get) => {
    let historyGeneration = 0;
    let historyLoadMorePromise: Promise<void> | null = null;
    let historyLoadMoreToken: symbol | null = null;
    let activeSessionRefreshGeneration = 0;
    let activeSessionRefreshPromise: Promise<void> | null = null;
    let activeSessionRefreshFollowUp = false;
    let sessionStartGeneration = 0;
    let pendingSessionStart: {
      readonly key: string;
      readonly promise: Promise<AgentSession>;
    } | null = null;
    const sessionStartRequestIds = new Map<string, string>();
    function applyTo(sid: string, event: AgentEvent): boolean {
      let applied = false;
      set((state) => {
        const cur = state.sessions[sid];
        if (!cur) return state;
        const result = reduceAgentEvent(cur, event);
        if (result.kind === "duplicate") return state;
        applied = true;
        if (result.kind === "session_exited") {
          const sessions = { ...state.sessions };
          delete sessions[sid];
          const closingSessionIds = new Set(state.closingSessionIds);
          closingSessionIds.delete(sid);
          const activeSid = state.activeSid === sid ? null : state.activeSid;
          return { ...state, sessions, closingSessionIds, activeSid };
        }
        return {
          ...state,
          sessions: { ...state.sessions, [sid]: result.session },
          activeSid: state.activeSid,
        };
      });
      return applied;
    }

    const agentLive = createAgentLiveController(
      {
        getSession: (sid) => get().sessions[sid],
        applyEvent: (sid, event) => {
          return applyTo(sid, event);
        },
        updateSession: (sid, update) => {
          set((state) => {
            const current = state.sessions[sid];
            if (!current) return state;
            return {
              ...state,
              sessions: {
                ...state.sessions,
                [sid]: update(current),
              },
            };
          });
        },
        updateAllSessions: (update) => {
          set((state) => ({
            ...state,
            sessions: Object.fromEntries(
              Object.entries(state.sessions).map(([sid, session]) => [
                sid,
                update(session),
              ]),
            ),
          }));
        },
        materializeSession: async (sid, firstEvent) => {
          const result = await listActiveSessions();
          const backend = result.sessions.find(
            (session) =>
              session.session_id === sid &&
              (session.session_kind ?? "user") === "user",
          );
          if (!backend) return false;
          set((state) => {
            if (state.sessions[sid]) return state;
            let materialized = createReconciledSessionState(backend);
            if (firstEvent.type !== "turn_start") {
              materialized = materializeCurrentRootTurn(materialized);
            }
            return {
              ...state,
              sessions: {
                ...state.sessions,
                [sid]: {
                  ...materialized,
                  lastSeq: null,
                  liveState: "reconnecting",
                  needsReplay: false,
                },
              },
            };
          });
          return true;
        },
        reconcileSessions: async () => {
          await get().refreshActiveSessions();
        },
      },
      { readinessTimeoutMs: options.liveReadinessTimeoutMs },
    );

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
      patchSession(sid, materializeCurrentRootTurn);
      try {
        const requests = await listAgentRequests(sid);
        for (const request of requests) applyApprovalRequest(sid, request);
      } catch {
        // 恢复失败不清除现有请求，等待下一次 SSE 或显式重试。
      }
    }

    function patchSession(
      sid: string,
      fn: (s: PerSessionState) => Partial<PerSessionState>,
    ): void {
      set((state) => {
        const cur = state.sessions[sid];
        if (!cur) return state;
        return {
          ...state,
          sessions: { ...state.sessions, [sid]: { ...cur, ...fn(cur) } },
        };
      });
    }

    function patchActive(
      fn: (s: PerSessionState) => Partial<PerSessionState>,
    ): void {
      const sid = get().activeSid;
      if (sid) patchSession(sid, fn);
    }

    /** 捕获某操作上一次失败；成功回调只允许清除这个对象本身。 */
    function previousProblemForOperation(
      sid: string,
      operation: string,
    ): AgentTransportProblem | null {
      const problem = get().sessions[sid]?.transportProblem ?? null;
      return problem?.operation === operation ? problem : null;
    }

    /** 清除本次重试已修复的问题，同时保留等待期间由并发操作写入的新问题。 */
    function clearProblemIfUnchanged(
      sid: string,
      previousProblem: AgentTransportProblem | null,
    ): void {
      if (!previousProblem) return;
      set((state) => {
        const session = state.sessions[sid];
        if (!session || session.transportProblem !== previousProblem) {
          return state;
        }
        return {
          ...state,
          sessions: {
            ...state.sessions,
            [sid]: { ...session, ...CLEARED_TRANSPORT_ISSUE },
          },
        };
      });
    }

    /** 收口尚未被 runtime 接受的乐观 turn，并保留启动失败原因。 */
    function failOptimisticTurn(sid: string, error: unknown): void {
      const problem = agentProblemFromUnknown(error, {
        code: "request_timeout",
        operation: "turn_start",
        budgetMs: null,
      });
      const acceptanceUnknown = problem.code === "turn_acceptance_unknown";
      set((state) => {
        const session = state.sessions[sid];
        if (!session) return state;
        const turns = session.turns.map((item, index) =>
          !acceptanceUnknown && index === session.turns.length - 1
            ? { ...item, status: "error" as const }
            : item,
        );
        return {
          ...state,
          sessions: {
            ...state.sessions,
            [sid]: {
              ...session,
              turns,
              phase: acceptanceUnknown ? session.phase : "error",
              abort: null,
              turnState: acceptanceUnknown ? "unknown" : "failed",
              transportError: problem.message,
              transportProblem: problem,
            },
          },
        };
      });
      if (acceptanceUnknown) void get().refreshActiveSessions();
    }

    /** 请求关闭尚未发送消息的后端会话，并保留可重试的失败原因。 */
    async function closeTemporaryBackendSession(
      sid: string,
    ): Promise<string | null> {
      try {
        const result = await apiDeleteSession(sid);
        if (result.status === "needs_reconcile") {
          return result.error ?? "会话资源尚未完全关闭，请重试。";
        }
        return null;
      } catch (error) {
        return error instanceof Error ? error.message : String(error);
      }
    }

    /** 把关闭失败的临时会话留在多开栏，避免后端连接变成不可见占位。 */
    function keepTemporaryCloseFailure(sid: string, error: string): void {
      const problem = agentProblemFromUnknown(new Error(error), {
        code: "close_needs_reconcile",
        operation: "session_close",
        budgetMs: null,
      });
      set((state) => {
        const session = state.sessions[sid];
        if (!session) return state;
        return {
          ...state,
          sessions: {
            ...state.sessions,
            [sid]: {
              ...session,
              connected: true,
              resourceState: "needs_reconcile",
              transportError: error,
              transportProblem: problem,
            },
          },
        };
      });
    }

    /** 为已连接会话恢复常驻事件流，并按需读取 Codex Goal。 */
    async function restoreMaterializedAgentLive(sid: string): Promise<void> {
      const session = get().sessions[sid];
      if (!session) return;
      if (session.runtime === "codex") {
        if (session.nativeSessionId === null) return;
      } else if (!session.connected) {
        return;
      }
      try {
        await agentLive.ensureWatcher();
      } catch {
        // session snapshot 已经是权威资源事实；live 暂不可用不能让物化整体失败。
        return;
      }
      await agentLive.refreshCodexGoal(sid);
    }

    /**
     * 用持久化历史补齐实时流缺口，再把活动快照的生命周期水位盖回去。
     * history 与 live 的 seq 空间相互独立，不能把 history 尾号当 live 水位。
     */
    async function reconcileSessionHistory(
      sid: string,
      snapshot: AgentSession,
      retryCount = 0,
    ): Promise<void> {
      let envelopes: readonly AgentEventLike[];
      try {
        envelopes = await getAgentHistory(sid);
      } catch {
        return;
      }
      let snapshotBecameStale = false;
      set((state) => {
        const current = state.sessions[sid];
        if (!current) return state;
        const snapshotSeq = snapshot.last_event_seq ?? null;
        const liveAdvancedAfterSnapshot =
          current.lastSeq !== null &&
          (snapshotSeq === null || current.lastSeq > snapshotSeq);
        if (liveAdvancedAfterSnapshot) {
          snapshotBecameStale = true;
          return state;
        }
        const replayed = replayAgentHistory(current, envelopes);
        const reconciled = reconcileCurrentRootTurn({
          ...replayed,
          connected: snapshot.connected,
          resourceState: snapshot.resource_state ?? current.resourceState,
          turnState:
            snapshot.turn_state ??
            (snapshot.running ? "running" : current.turnState),
          currentTurnId:
            snapshot.current_turn_id !== undefined
              ? snapshot.current_turn_id
              : current.currentTurnId,
          stateGeneration:
            snapshot.state_generation ?? current.stateGeneration,
          lastSeq: snapshotSeq,
          liveState: current.liveState,
          needsReplay: false,
          transportError: isLiveTransportProblem(current.transportProblem)
            ? null
            : current.transportError,
          transportProblem: isLiveTransportProblem(current.transportProblem)
            ? null
            : current.transportProblem,
        });
        return {
          ...state,
          sessions: {
            ...state.sessions,
            [sid]: reconciled,
          },
        };
      });
      if (!snapshotBecameStale || retryCount >= 1) return;
      try {
        const latest = (await listActiveSessions()).sessions.find(
          (session) => session.session_id === sid,
        );
        if (latest) await reconcileSessionHistory(sid, latest, retryCount + 1);
      } catch {
        // 保留 needsReplay，等待下一次 live control 或显式刷新再对账。
      }
    }

    async function dropTempActive(): Promise<void> {
      const state = get();
      const sid = state.activeSid;
      if (!sid) return;
      const s = state.sessions[sid];
      if (!s || s.connected || s.meta.exited || s.abort) return;
      const closeError = await closeTemporaryBackendSession(sid);
      if (closeError) {
        keepTemporaryCloseFailure(sid, closeError);
        return;
      }
      set((st) => {
        // await 期间活动会话可能已切换，不能删除新会话。
        if (st.activeSid !== sid) return st;
        const sessions = { ...st.sessions };
        delete sessions[sid];
        return { ...st, sessions, activeSid: null };
      });
    }

    /** 执行一次真实创建；同参数重复点击由公开 action 复用这一个 Promise。 */
    async function startSessionOnce(
      params: StartSessionParams,
      requestId: string,
    ): Promise<AgentSession> {
      const generation = ++sessionStartGeneration;
      // 未连接的临时会话不计入并发上限，切换前直接丢弃。
      await dropTempActive();
      const runtime: Runtime = params.runtime ?? "claude_code";
      const session = await apiCreateSession({ ...params, runtime }, requestId);
      const sid = session.session_id;
      if (generation !== sessionStartGeneration) {
        const closeError = await closeTemporaryBackendSession(sid);
        if (closeError) {
          const failedSession = {
            ...createNewSessionState(session, params),
            connected: true,
            resourceState: "needs_reconcile" as const,
            transportError: closeError,
            transportProblem: closeProblem(closeError),
          };
          set((state) => ({
            ...state,
            sessions: {
              ...state.sessions,
              [sid]: state.sessions[sid] ?? failedSession,
            },
          }));
        }
        return session;
      }
      const perSession = createNewSessionState(session, params);
      set((state) => ({
        ...state,
        sessions: { ...state.sessions, [sid]: perSession },
        activeSid: sid,
      }));
      await restoreMaterializedAgentLive(sid);
      return session;
    }

    return {
      sessions: {},
      closingSessionIds: new Set<string>(),
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
        const key = sessionStartKey(params);
        if (pendingSessionStart?.key === key)
          return pendingSessionStart.promise;
        const requestId =
          sessionStartRequestIds.get(key) ?? createSessionRequestId();
        sessionStartRequestIds.set(key, requestId);
        const promise = startSessionOnce(params, requestId);
        pendingSessionStart = { key, promise };
        try {
          const session = await promise;
          sessionStartRequestIds.delete(key);
          return session;
        } finally {
          if (pendingSessionStart?.promise === promise)
            pendingSessionStart = null;
        }
      },

      activateSession: async (sid) => {
        const state = get();
        if (!state.sessions[sid]) return;
        if (state.activeSid === sid) {
          await recoverApprovalRequests(sid);
          await restoreMaterializedAgentLive(sid);
          return;
        }
        await dropTempActive();
        set({ activeSid: sid });
        await recoverApprovalRequests(sid);
        await restoreMaterializedAgentLive(sid);
      },

      showWorkspaceHome: () => {
        const state = get();
        const sid = state.activeSid;
        if (!sid) return;
        const session = state.sessions[sid];
        set({ activeSid: null });
        if (
          !session ||
          session.connected ||
          session.meta.exited ||
          session.abort
        )
          return;
        void (async () => {
          const closeError = await closeTemporaryBackendSession(sid);
          if (closeError) {
            keepTemporaryCloseFailure(sid, closeError);
            return;
          }
          set((current) => {
            const stale = current.sessions[sid];
            if (
              current.activeSid === sid ||
              !stale ||
              stale.connected ||
              stale.abort
            ) {
              return current;
            }
            const sessions = { ...current.sessions };
            delete sessions[sid];
            return { ...current, sessions };
          });
        })();
      },

      closeSession: async (sid) => {
        const current = get();
        const cur = current.sessions[sid];
        if (!cur) return;
        if (current.closingSessionIds.has(sid)) return;
        set((state) => ({
          ...state,
          closingSessionIds: new Set(state.closingSessionIds).add(sid),
          sessions: {
            ...state.sessions,
            [sid]: { ...cur, resourceState: "closing" },
          },
        }));
        cur.abort?.abort();
        agentLive.stop(sid);
        try {
          const result = await apiDeleteSession(sid);
          if (result.status === "needs_reconcile") {
            set((state) => {
              const session = state.sessions[sid];
              const closingSessionIds = new Set(state.closingSessionIds);
              closingSessionIds.delete(sid);
              if (!session) return { ...state, closingSessionIds };
              return {
                ...state,
                closingSessionIds,
                sessions: {
                  ...state.sessions,
                  [sid]: {
                    ...session,
                    resourceState: "needs_reconcile",
                    turnState: isRootTurnInFlight(session)
                      ? "unknown"
                      : session.turnState,
                    abort: null,
                    transportError:
                      result.error ?? "会话资源尚未完全关闭，请重试。",
                    transportProblem: closeProblem(
                      result.error ?? "会话资源尚未完全关闭，请重试。",
                    ),
                  },
                },
              };
            });
            void get().refreshActiveSessions();
            return;
          }
        } catch (error) {
          set((state) => {
            const session = state.sessions[sid];
            const closingSessionIds = new Set(state.closingSessionIds);
            closingSessionIds.delete(sid);
            if (!session) return { ...state, closingSessionIds };
            return {
              ...state,
              closingSessionIds,
              sessions: {
                ...state.sessions,
                [sid]: {
                  ...session,
                  resourceState: "needs_reconcile",
                  turnState: isRootTurnInFlight(session)
                    ? "unknown"
                    : session.turnState,
                  abort: null,
                  transportError:
                    error instanceof Error ? error.message : String(error),
                  transportProblem: agentProblemFromUnknown(error, {
                    code: "close_needs_reconcile",
                    operation: "session_close",
                    budgetMs: null,
                  }),
                },
              },
            };
          });
          void get().refreshActiveSessions();
          return;
        }
        set((state) => {
          const sessions = { ...state.sessions };
          delete sessions[sid];
          const closingSessionIds = new Set(state.closingSessionIds);
          closingSessionIds.delete(sid);
          const activeSid = state.activeSid === sid ? null : state.activeSid;
          return { ...state, sessions, closingSessionIds, activeSid };
        });
      },

      refreshActiveSessions: async () => {
        if (activeSessionRefreshPromise) {
          activeSessionRefreshFollowUp = true;
          return activeSessionRefreshPromise;
        }
        const refresh = (async () => {
          activeSessionRefreshFollowUp = false;
          let liveReady = false;
          let refreshBatchCount = 0;
          // 必须等应用 SSE 真正 ready 后再读 session 快照。只启动后台 Promise 仍会留下
          // “快照已读、订阅尚未建立”的窗口，跨 renderer 创建的 session 会同时漏过两边。
          try {
            await agentLive.ensureWatcher();
            liveReady = true;
          } catch {
            // 实时流不可用时仍读取权威快照，让已有 session 保持可见并展示 live 错误。
          }
          while (true) {
            const generation = ++activeSessionRefreshGeneration;
            let backend: readonly AgentSession[];
            try {
              const result = await listActiveSessions();
              backend = result.sessions;
            } catch {
              break;
            }
            if (generation !== activeSessionRefreshGeneration) break;
            const userSessions = backend.filter(
              (session) => (session.session_kind ?? "user") === "user",
            );
            const newConnectedAgentSessions: string[] = [];
            const historyReplays: AgentSession[] = [];
            set((state) => {
              const merged = Object.fromEntries(
                Object.entries(state.sessions).filter(
                  ([, session]) => session.sessionKind === "delegate",
                ),
              );
              for (const b of userSessions) {
                const existing = state.sessions[b.session_id];
                if (existing) {
                  if (!existing.connected && b.connected) {
                    newConnectedAgentSessions.push(b.session_id);
                  }
                  const displayTitle = b.display_title ?? existing.displayTitle;
                  const titleSource = b.title_source ?? existing.titleSource;
                  const incomingGeneration = b.state_generation ?? 0;
                  const acceptsSnapshot =
                    incomingGeneration >= existing.stateGeneration;
                  const incomingSeq = b.last_event_seq ?? null;
                  const requiresReplay =
                    acceptsSnapshot &&
                    (existing.needsReplay ||
                      existing.liveState === "gapped" ||
                      (incomingSeq !== null &&
                        incomingSeq !== existing.lastSeq));
                  if (requiresReplay) historyReplays.push(b);
                  merged[b.session_id] = reconcileCurrentRootTurn({
                    ...existing,
                    displayTitle,
                    titleSource,
                    connected: b.connected,
                    resourceState: acceptsSnapshot
                      ? (b.resource_state ?? existing.resourceState)
                      : existing.resourceState,
                    turnState: acceptsSnapshot
                      ? (b.turn_state ??
                        (b.running ? "running" : existing.turnState))
                      : existing.turnState,
                    currentTurnId: acceptsSnapshot
                      ? b.current_turn_id !== undefined
                        ? b.current_turn_id
                        : existing.currentTurnId
                      : existing.currentTurnId,
                    stateGeneration: acceptsSnapshot
                      ? incomingGeneration
                      : existing.stateGeneration,
                    lastSeq:
                      acceptsSnapshot && !requiresReplay
                        ? (incomingSeq ?? existing.lastSeq)
                        : existing.lastSeq,
                    liveState:
                      acceptsSnapshot && !requiresReplay
                        ? "ready"
                        : existing.liveState,
                    needsReplay: requiresReplay || existing.needsReplay,
                    transportError:
                      acceptsSnapshot &&
                      !requiresReplay &&
                      isLiveTransportProblem(existing.transportProblem)
                        ? null
                        : existing.transportError,
                    transportProblem:
                      acceptsSnapshot &&
                      !requiresReplay &&
                      isLiveTransportProblem(existing.transportProblem)
                        ? null
                        : existing.transportProblem,
                  });
                } else {
                  const materialized = createReconciledSessionState(b);
                  const requiresReplay = b.last_event_seq != null;
                  merged[b.session_id] = requiresReplay
                    ? {
                        ...materialized,
                        lastSeq: null,
                        liveState: "reconnecting",
                        needsReplay: true,
                      }
                    : materialized;
                  if (requiresReplay) historyReplays.push(b);
                  if (b.connected) {
                    newConnectedAgentSessions.push(b.session_id);
                  }
                }
              }
              const activeSid =
                state.activeSid && merged[state.activeSid]
                  ? state.activeSid
                  : null;
              return { ...state, sessions: merged, activeSid };
            });
            if (liveReady) {
              await runWithConcurrency(
                newConnectedAgentSessions,
                3,
                restoreMaterializedAgentLive,
              );
            }
            await runWithConcurrency(historyReplays, 3, async (snapshot) =>
              reconcileSessionHistory(snapshot.session_id, snapshot),
            );
            await runWithConcurrency(
              userSessions
                .filter(
                  (session) =>
                    session.runtime === "codex" && session.connected,
                )
                .map((session) => session.session_id),
              3,
              recoverApprovalRequests,
            );
            if (
              liveReady &&
              (newConnectedAgentSessions.length > 0 ||
                historyReplays.length > 0)
            ) {
              await agentLive.ensureWatcher();
            }
            refreshBatchCount += 1;
            const shouldRunFollowUp =
              activeSessionRefreshFollowUp && refreshBatchCount < 2;
            activeSessionRefreshFollowUp = false;
            if (!shouldRunFollowUp) break;
          }
        })();
        activeSessionRefreshPromise = refresh;
        try {
          await refresh;
        } finally {
          if (activeSessionRefreshPromise === refresh) {
            activeSessionRefreshPromise = null;
          }
        }
      },

      refreshHistory: async (workdir) => {
        const issueOwnerSid = get().activeSid;
        const previousProblem = issueOwnerSid
          ? previousProblemForOperation(issueOwnerSid, "history_list")
          : null;
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
          if (issueOwnerSid) {
            clearProblemIfUnchanged(issueOwnerSid, previousProblem);
          }
        } catch (err) {
          if (generation !== historyGeneration) return;
          set({
            loadingHistory: false,
            historyError: (err as Error).message,
          });
          if (issueOwnerSid) {
            patchSession(issueOwnerSid, () =>
              transportIssueFromProblem(
                problemForOperation(err, "history_list"),
              ),
            );
          }
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
          const selection = await apiUpdateSessionSettings(sid, {
            model,
            effort,
          });
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
                  ...CLEARED_TRANSPORT_ISSUE,
                },
              },
            };
          });
        } catch (err) {
          patchActive(() => transportIssueFromMessage((err as Error).message));
        }
      },

      selectSessionPermissionPreset: async (preset) => {
        const sid = get().activeSid;
        if (!sid) return;
        const current = get().sessions[sid];
        if (!current || current.runtime !== "codex" || current.abort) return;
        try {
          const result = await apiUpdatePermissionPreset(sid, preset);
          set((state) => {
            const session = state.sessions[sid];
            if (!session) return state;
            return {
              ...state,
              sessions: {
                ...state.sessions,
                [sid]: {
                  ...session,
                  // requested preset 立即更新；effective facts 仍以原生响应为准。
                  permissionPreset: result.permission_preset,
                  settingsNotice: "将在下一轮生效",
                  ...CLEARED_TRANSPORT_ISSUE,
                },
              },
            };
          });
        } catch (err) {
          patchActive(() => transportIssueFromMessage((err as Error).message));
        }
      },

      loadHistoryIntoView: async () => {
        const sid = get().activeSid;
        if (!sid) return;
        const cur = get().sessions[sid];
        if (!cur) return;
        // 活动快照比可能尚未封口的 history 更新；in-flight 时回放会抹掉
        // 当前审批、提问或尚未落盘的局部输出。
        if (isRootTurnInFlight(cur)) return;
        const liveSeqAtRequest = cur.lastSeq;
        let envelopes: readonly AgentEventLike[] = [];
        try {
          envelopes = await getAgentHistory(sid);
        } catch {
          // 不支持回放或网络失败时保留当前视图。
          return;
        }
        set((state) => {
          const s = state.sessions[sid];
          if (!s || s.lastSeq !== liveSeqAtRequest) return state;
          return {
            ...state,
            sessions: {
              ...state.sessions,
              [sid]: replayAgentHistory(s, envelopes),
            },
          };
        });
        if (get().activeSid !== sid) return;
        const childThreadIds = Object.keys(
          get().sessions[sid]?.codexSubagents ?? {},
        );
        await Promise.all(
          childThreadIds.map((threadId) =>
            get().loadCodexSubagentHistory(threadId),
          ),
        );
      },

      loadCodexSubagentHistory: async (threadId) => {
        const sid = get().activeSid;
        if (!sid) return;
        const session = get().sessions[sid];
        const child = session?.codexSubagents[threadId];
        if (!session || session.runtime !== "codex" || !child) return;
        if (child.historyLoaded || child.historyLoading) return;
        const stateAtRequest = child.state;
        set((state) => {
          const current = state.sessions[sid];
          const target = current?.codexSubagents[threadId];
          if (!current || !target) return state;
          return {
            ...state,
            sessions: {
              ...state.sessions,
              [sid]: {
                ...current,
                codexSubagents: {
                  ...current.codexSubagents,
                  [threadId]: {
                    ...target,
                    historyLoading: true,
                    historyError: null,
                  },
                },
              },
            },
          };
        });
        try {
          const envelopes = await getCodexSubagentHistory(sid, threadId);
          set((state) => {
            const current = state.sessions[sid];
            const target = current?.codexSubagents[threadId];
            if (!current || !target) return state;
            if (target.state !== stateAtRequest) {
              return {
                ...state,
                sessions: {
                  ...state.sessions,
                  [sid]: {
                    ...current,
                    codexSubagents: {
                      ...current.codexSubagents,
                      [threadId]: { ...target, historyLoading: false },
                    },
                  },
                },
              };
            }
            return {
              ...state,
              sessions: {
                ...state.sessions,
                [sid]: replayCodexSubagentHistory(current, threadId, envelopes),
              },
            };
          });
          if (get().activeSid !== sid) return;
          const nestedThreadIds = Object.values(
            get().sessions[sid]?.codexSubagents ?? {},
          )
            .filter((candidate) => candidate.parentThreadId === threadId)
            .map((candidate) => candidate.threadId);
          await Promise.all(
            nestedThreadIds.map((nestedThreadId) =>
              get().loadCodexSubagentHistory(nestedThreadId),
            ),
          );
        } catch (error) {
          set((state) => {
            const current = state.sessions[sid];
            const target = current?.codexSubagents[threadId];
            if (!current || !target) return state;
            return {
              ...state,
              sessions: {
                ...state.sessions,
                [sid]: {
                  ...current,
                  codexSubagents: {
                    ...current.codexSubagents,
                    [threadId]: {
                      ...target,
                      historyLoading: false,
                      historyError:
                        error instanceof Error ? error.message : String(error),
                    },
                  },
                },
              },
            };
          });
        }
      },

      renameSessionTitle: async (sid, title) => {
        const normalized = title.trim();
        if (!normalized) return;
        try {
          const updated = await apiRenameSessionTitle(sid, normalized);
          set((state) => {
            const session = state.sessions[sid];
            if (!session) return state;
            return {
              ...state,
              sessions: {
                ...state.sessions,
                [sid]: {
                  ...session,
                  displayTitle: updated.display_title ?? normalized,
                  titleSource: updated.title_source ?? "manual",
                  ...CLEARED_TRANSPORT_ISSUE,
                },
              },
            };
          });
        } catch (error) {
          set((state) => {
            const session = state.sessions[sid];
            if (!session) return state;
            return {
              ...state,
              sessions: {
                ...state.sessions,
                [sid]: {
                  ...session,
                  ...transportIssueFromMessage(
                    error instanceof Error ? error.message : String(error),
                  ),
                },
              },
            };
          });
        }
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
          const admission = admitSessionSend(state.sessions, sid, turn, abort);
          accepted = admission.accepted;
          if (admission.sessions === state.sessions) return state;
          return { ...state, sessions: admission.sessions };
        });
        if (!accepted) return;

        const admitted = get().sessions[sid];
        if (admitted?.titleSource === "new") {
          const fallback = promptSessionTitle(text);
          if (fallback) {
            set((state) => {
              const session = state.sessions[sid];
              if (!session || session.titleSource !== "new") return state;
              return {
                ...state,
                sessions: {
                  ...state.sessions,
                  [sid]: {
                    ...session,
                    displayTitle: fallback,
                    titleSource: "prompt",
                  },
                },
              };
            });
            void apiGenerateSessionTitle(sid, text)
              .then((updated) => {
                set((state) => {
                  const session = state.sessions[sid];
                  if (
                    !session ||
                    session.titleSource !== "prompt" ||
                    session.displayTitle !== fallback
                  ) {
                    return state;
                  }
                  return {
                    ...state,
                    sessions: {
                      ...state.sessions,
                      [sid]: {
                        ...session,
                        displayTitle: updated.display_title ?? fallback,
                        titleSource: updated.title_source ?? "prompt",
                      },
                    },
                  };
                });
              })
              .catch(() => {
                // 标题是旁路增强；失败时保留已显示的首条提示词。
              });
          }
        }

        try {
          await agentLive.ensureWatcher();
          const accepted = await apiStartAgentTurn(sid, text);
          if (accepted.turnId !== null) {
            set((state) => {
              const session = state.sessions[sid];
              if (!session || session.turnState !== "starting") return state;
              return {
                ...state,
                sessions: {
                  ...state.sessions,
                  [sid]: {
                    ...session,
                    currentTurnId: accepted.turnId,
                    turnState: "running",
                  },
                },
              };
            });
          }
        } catch (error) {
          failOptimisticTurn(sid, error);
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
          const problem = agentProblemFromUnknown(err, {
            code: "turn_state_unknown",
            operation: "turn_interrupt",
            budgetMs: null,
          });
          patchActive((session) => ({
            turnState:
              problem.code === "turn_state_unknown"
                ? "unknown"
                : session.turnState,
            transportError: problem.message,
            transportProblem: problem,
          }));
          if (problem.code === "turn_state_unknown") {
            void get().refreshActiveSessions();
          }
        }
      },

      answerElicit: async (answers) => {
        const sid = get().activeSid;
        if (!sid) return;
        const previousProblem = previousProblemForOperation(
          sid,
          "elicitation_answer",
        );
        try {
          await apiAnswerElicit(sid, { answers, cancel: false });
          clearProblemIfUnchanged(sid, previousProblem);
        } catch (err) {
          patchSession(sid, () =>
            transportIssueFromProblem(
              problemForOperation(err, "elicitation_answer"),
            ),
          );
        }
      },

      cancelElicit: async () => {
        const sid = get().activeSid;
        if (!sid) return;
        const previousProblem = previousProblemForOperation(
          sid,
          "elicitation_answer",
        );
        try {
          await apiAnswerElicit(sid, { answers: {}, cancel: true });
          patchSession(sid, (session) =>
            declinePendingElicitation(session),
          );
          clearProblemIfUnchanged(sid, previousProblem);
        } catch (err) {
          patchSession(sid, () =>
            transportIssueFromProblem(
              problemForOperation(err, "elicitation_answer"),
            ),
          );
        }
      },

      answerApproval: async (requestId, decision) => {
        const sid = get().activeSid;
        if (!sid) return;
        const session = get().sessions[sid];
        if (session?.runtime !== "codex") return;
        const previousProblem = previousProblemForOperation(
          sid,
          "approval_answer",
        );
        try {
          const result = await apiAnswerAgentRequest(sid, requestId, decision);
          applyApprovalRequest(sid, result.request);
          clearProblemIfUnchanged(sid, previousProblem);
        } catch (err) {
          set((state) => {
            const current = state.sessions[sid];
            if (!current) return state;
            return {
              ...state,
              sessions: {
                ...state.sessions,
                [sid]: {
                  ...current,
                  ...transportIssueFromProblem(
                    problemForOperation(err, "approval_answer"),
                  ),
                },
              },
            };
          });
        }
      },

      setCodexGoal: async (update) => {
        const sid = get().activeSid;
        if (!sid || get().sessions[sid]?.runtime !== "codex") return;
        try {
          const goal = await apiSetCodexGoal(sid, update);
          set((state) => {
            const session = state.sessions[sid];
            if (!session) return state;
            return {
              ...state,
              sessions: {
                ...state.sessions,
                [sid]: { ...session, goal, ...CLEARED_TRANSPORT_ISSUE },
              },
            };
          });
        } catch (error) {
          patchActive(() =>
            transportIssueFromMessage((error as Error).message),
          );
        }
      },

      clearCodexGoal: async () => {
        const sid = get().activeSid;
        if (!sid || get().sessions[sid]?.runtime !== "codex") return;
        try {
          await apiClearCodexGoal(sid);
          set((state) => {
            const session = state.sessions[sid];
            if (!session) return state;
            return {
              ...state,
              sessions: {
                ...state.sessions,
                [sid]: {
                  ...session,
                  goal: null,
                  ...CLEARED_TRANSPORT_ISSUE,
                },
              },
            };
          });
        } catch (error) {
          patchActive(() =>
            transportIssueFromMessage((error as Error).message),
          );
        }
      },

      compactCodex: async () => {
        const sid = get().activeSid;
        if (!sid) throw new Error("No active Codex session");
        let accepted = false;
        let phaseBeforeCompact: PerSessionState["phase"] | null = null;
        set((state) => {
          const session = state.sessions[sid];
          if (
            !session ||
            session.runtime !== "codex" ||
            isRootTurnInFlight(session) ||
            session.commandPending
          ) {
            return state;
          }
          accepted = true;
          phaseBeforeCompact = session.phase;
          return {
            ...state,
            sessions: {
              ...state.sessions,
              [sid]: {
                ...session,
                commandPending: "compact",
                phase: "compacting",
                turnState: "starting",
                currentTurnId: null,
                ...CLEARED_TRANSPORT_ISSUE,
              },
            },
          };
        });
        if (!accepted)
          throw new Error("/compact is unavailable for this session");
        try {
          await agentLive.ensureWatcher();
          await apiCompactCodexSession(sid);
        } catch (error) {
          set((state) => {
            const session = state.sessions[sid];
            if (!session) return state;
            return {
              ...state,
              sessions: {
                ...state.sessions,
                [sid]: {
                  ...session,
                  commandPending: null,
                  turnState: "idle",
                  phase:
                    session.phase === "compacting"
                      ? (phaseBeforeCompact ?? "idle")
                      : session.phase,
                  ...transportIssueFromMessage((error as Error).message),
                },
              },
            };
          });
          throw error;
        }
      },

      startCodexReview: async (target) => {
        const sid = get().activeSid;
        if (!sid) throw new Error("No active Codex session");
        let accepted = false;
        set((state) => {
          const session = state.sessions[sid];
          if (
            !session ||
            session.runtime !== "codex" ||
            isRootTurnInFlight(session) ||
            session.commandPending
          ) {
            return state;
          }
          accepted = true;
          return {
            ...state,
            sessions: {
              ...state.sessions,
              [sid]: {
                ...session,
                commandPending: "review",
                turnState: "starting",
                currentTurnId: null,
                ...CLEARED_TRANSPORT_ISSUE,
              },
            },
          };
        });
        if (!accepted)
          throw new Error("/review is unavailable for this session");
        try {
          await agentLive.ensureWatcher();
          const result = await apiStartCodexReview(sid, target);
          set((state) => {
            const session = state.sessions[sid];
            if (!session) return state;
            const alreadyObserved = session.turns.some(
              (turn) => turn.turnId === result.turnId,
            );
            const observedTurn = session.turns.find(
              (turn) => turn.turnId === result.turnId,
            );
            const terminalAlreadyObserved =
              observedTurn !== undefined && observedTurn.status !== "active";
            const reduced = alreadyObserved
              ? session
              : reduceEvent(session, {
                  type: "turn_start",
                  turn_id: result.turnId,
                  autonomous: true,
                  revertible: false,
                });
            return {
              ...state,
              sessions: {
                ...state.sessions,
                [sid]: {
                  ...session,
                  ...reduced,
                  commandPending: null,
                  abort: terminalAlreadyObserved
                    ? null
                    : alreadyObserved
                      ? session.abort
                      : (session.abort ?? new AbortController()),
                  currentTurnId: result.turnId,
                  turnState: terminalAlreadyObserved
                    ? session.turnState
                    : "running",
                  connected: true,
                  ...CLEARED_TRANSPORT_ISSUE,
                },
              },
            };
          });
        } catch (error) {
          set((state) => {
            const session = state.sessions[sid];
            if (!session) return state;
            return {
              ...state,
              sessions: {
                ...state.sessions,
                [sid]: {
                  ...session,
                  commandPending: null,
                  turnState: "idle",
                  ...transportIssueFromMessage((error as Error).message),
                },
              },
            };
          });
          throw error;
        }
      },

      revertTurn: async (turnId) => {
        const sid = get().activeSid;
        if (!sid) return;
        const cur = get().sessions[sid];
        // 流式写入期间回退会与后端落盘竞争。
        if (!cur || isRootTurnInFlight(cur)) return;
        const previousProblem = previousProblemForOperation(
          sid,
          "session_revert",
        );
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
                  ...(s.transportProblem === previousProblem && previousProblem
                    ? CLEARED_TRANSPORT_ISSUE
                    : {}),
                },
              },
            };
          });
        } catch (err) {
          patchSession(sid, () =>
            transportIssueFromProblem(
              problemForOperation(err, "session_revert"),
            ),
          );
        }
      },

      reset: () => {
        sessionStartGeneration += 1;
        pendingSessionStart = null;
        for (const s of Object.values(get().sessions)) {
          s.abort?.abort();
        }
        agentLive.stopAll();
        set({
          sessions: {},
          closingSessionIds: new Set<string>(),
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

export const useAgentStore = createAgentStore();

/** 以固定并发数执行后台恢复，避免占满浏览器同源连接池。 */
async function runWithConcurrency<T>(
  items: readonly T[],
  concurrency: number,
  worker: (item: T) => Promise<void>,
): Promise<void> {
  let nextIndex = 0;
  async function consume(): Promise<void> {
    while (nextIndex < items.length) {
      const item = items[nextIndex];
      nextIndex += 1;
      await worker(item);
    }
  }
  const workerCount = Math.min(concurrency, items.length);
  await Promise.all(Array.from({ length: workerCount }, () => consume()));
}

/** 为尚未完成的新建请求生成稳定键，防止双击产生两个后端会话。 */
function sessionStartKey(params: StartSessionParams): string {
  return JSON.stringify(
    Object.entries(params).sort(([left], [right]) => left.localeCompare(right)),
  );
}

/** 生成一次逻辑创建跨超时重试复用的幂等请求 ID。 */
function createSessionRequestId(): string {
  return globalThis.crypto.randomUUID();
}

/** 为后端已经明确返回的清理失败创建可恢复问题。 */
function closeProblem(message: string): AgentTransportProblem {
  return agentProblemFromUnknown(new Error(message), {
    code: "close_needs_reconcile",
    operation: "session_close",
    budgetMs: null,
  });
}

/** 为尚未迁入 request policy 的旧调用补稳定 operation，同时保留已有错误码和状态。 */
function problemForOperation(
  error: unknown,
  operation: string,
): AgentTransportProblem {
  const problem = agentProblemFromUnknown(error, {
    code: "http_error",
    operation,
    budgetMs: null,
  });
  return problem.operation === operation ? problem : { ...problem, operation };
}

/** 判断问题是否会在实时流恢复并完成对账后自动消失。 */
function isLiveTransportProblem(
  problem: AgentTransportProblem | null | undefined,
): boolean {
  return (
    problem?.code === "live_unavailable" ||
    problem?.code === "live_disconnected" ||
    problem?.code === "event_gap" ||
    problem?.code === "turn_acceptance_unknown" ||
    problem?.code === "turn_state_unknown"
  );
}

/** 只订阅活动会话，后台会话更新不会触发中心区域重渲染。 */
export function useActiveSession(): PerSessionState | null {
  return useAgentStore((s) =>
    s.activeSid ? (s.sessions[s.activeSid] ?? null) : null,
  );
}
