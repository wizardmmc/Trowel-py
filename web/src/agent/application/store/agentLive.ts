/** 管理 renderer 唯一的应用级 Agent 事件流、重连和未知会话缓冲。 */

import { agentEventsUrl, getCodexGoal } from "../../transport/api";
import type { AgentEvent } from "../../transport/agentEvent";
import {
  getEventStream,
  type AgentStreamControl,
} from "../../transport/stream";
import {
  AgentTransportError,
  agentProblemFromUnknown,
} from "../../transport/httpError";
import {
  CLEARED_TRANSPORT_ISSUE,
  transportIssueFromProblem,
  type PerSessionState,
} from "./sessionState";

const UNKNOWN_SESSION_BUFFER_LIMIT = 128;
const RECONNECT_DELAY_MS = 500;
const LIVE_READINESS_TIMEOUT_MS = 10_000;

interface AgentLiveCallbacks {
  readonly getSession: (sid: string) => PerSessionState | undefined;
  readonly applyEvent: (sid: string, event: AgentEvent) => boolean;
  readonly updateSession: (
    sid: string,
    update: (session: PerSessionState) => PerSessionState,
  ) => void;
  readonly updateAllSessions: (
    update: (session: PerSessionState) => PerSessionState,
  ) => void;
  readonly materializeSession: (
    sid: string,
    firstEvent: AgentEvent,
  ) => Promise<boolean>;
  readonly reconcileSessions: () => Promise<void>;
}

interface AgentLiveControllerOptions {
  /** 等待应用 SSE ready 的上限；测试可缩短，生产使用固定预算。 */
  readonly readinessTimeoutMs?: number;
}

interface Watcher {
  readonly controller: AbortController;
  connected: boolean;
  ready: Promise<void>;
  resolveReady: () => void;
  rejectReady: (reason: unknown) => void;
}

/** 创建一个只持有单条应用 SSE 的实时连接控制器。 */
export function createAgentLiveController(
  callbacks: AgentLiveCallbacks,
  options: AgentLiveControllerOptions = {},
) {
  const readinessTimeoutMs =
    options.readinessTimeoutMs ?? LIVE_READINESS_TIMEOUT_MS;
  let watcher: Watcher | null = null;
  let streamGeneration: string | null = null;
  let reconciliation: Promise<void> | null = null;
  const bufferedEvents = new Map<string, AgentEvent[]>();
  const overflowedBuffers = new Set<string>();
  const materializing = new Set<string>();

  function routeEvent(event: AgentEvent): void {
    const sid = event.session_id;
    const existing = callbacks.getSession(sid);
    if (existing) {
      if (bufferedEvents.has(sid)) flushBuffered(sid);
      const neededReplayBefore =
        callbacks.getSession(sid)?.needsReplay ?? existing.needsReplay;
      callbacks.applyEvent(sid, event);
      reconcileAfterReducerGap(sid, neededReplayBefore);
      return;
    }
    const pending = bufferedEvents.get(sid) ?? [];
    if (pending.length >= UNKNOWN_SESSION_BUFFER_LIMIT) {
      pending.shift();
      overflowedBuffers.add(sid);
    }
    pending.push(event);
    bufferedEvents.set(sid, pending);
    if (materializing.has(sid)) return;
    materializing.add(sid);
    void callbacks
      .materializeSession(sid, event)
      .then((materialized) => {
        if (materialized) {
          flushBuffered(sid);
          return;
        }
        // snapshot 已确认该 user session 不存在，不能无限保留它的孤儿事件。
        bufferedEvents.delete(sid);
        overflowedBuffers.delete(sid);
      })
      .catch(() => {
        // 暂时读不到 snapshot 时保留缓冲；后续事件或重连对账会再次尝试物化。
      })
      .finally(() => {
        materializing.delete(sid);
      });
  }

  function flushBuffered(sid: string): void {
    const session = callbacks.getSession(sid);
    const pending = bufferedEvents.get(sid);
    if (!session || !pending) return;
    bufferedEvents.delete(sid);
    const overflowed = overflowedBuffers.delete(sid);
    const firstBufferedSeq = pending[0]?.seq ?? 1;
    const missingPrefix = session.lastSeq === null && firstBufferedSeq > 1;
    if (missingPrefix) {
      callbacks.updateSession(sid, (current) => ({
        ...current,
        lastSeq: firstBufferedSeq - 1,
        liveState: "gapped",
        turnState: "unknown",
        needsReplay: true,
      }));
    }
    if (overflowed) {
      callbacks.updateSession(sid, (current) => ({
        ...current,
        liveState: "gapped",
        turnState: "unknown",
        needsReplay: true,
      }));
    }
    let neededReplay = callbacks.getSession(sid)?.needsReplay ?? false;
    for (const event of pending) {
      callbacks.applyEvent(sid, event);
      reconcileAfterReducerGap(sid, neededReplay);
      neededReplay = callbacks.getSession(sid)?.needsReplay ?? neededReplay;
    }
    if (!missingPrefix && !overflowed) {
      callbacks.updateSession(sid, (current) => ({
        ...current,
        liveState:
          current.needsReplay || current.liveState === "gapped"
            ? current.liveState
            : "ready",
      }));
    }
  }

  function reconcileAfterReducerGap(
    sid: string,
    neededReplayBefore: boolean,
  ): void {
    const current = callbacks.getSession(sid);
    if (!neededReplayBefore && current?.needsReplay) {
      void reconcileContinuousLive();
    }
  }

  function handleControl(control: AgentStreamControl): void {
    if (control.type === "gap") {
      const problem = agentProblemFromUnknown(
        new Error("实时事件发生缺口，正在按后端事实恢复"),
        {
          code: "event_gap",
          operation: "agent_live_stream",
          budgetMs: null,
        },
      );
      callbacks.updateSession(control.sessionId, (session) => ({
        ...session,
        liveState: "gapped",
        turnState: "unknown",
        needsReplay: true,
        transportError: problem.message,
        transportProblem: problem,
      }));
      void reconcileContinuousLive();
      return;
    }
    applyReady(control.generation);
  }

  function applyReady(generation: string | null): void {
    const changed =
      streamGeneration !== null && generation !== streamGeneration;
    streamGeneration = generation;
    callbacks.updateAllSessions((session) => ({
      ...session,
      liveState: changed || session.needsReplay ? "reconnecting" : "ready",
      needsReplay: session.needsReplay || changed,
      turnState:
        changed && isUnfinished(session.turnState)
          ? "unknown"
          : session.turnState,
      // sidecar 重启后 seq 和状态代次都从新进程重新计数，旧水位不能与新
      // generation 比较，否则 snapshot 会被永久误判为迟到响应。
      lastSeq: changed ? null : session.lastSeq,
      stateGeneration: changed ? 0 : session.stateGeneration,
    }));
    if (changed) void reconcileContinuousLive();
  }

  async function reconcileContinuousLive(): Promise<void> {
    if (reconciliation) return reconciliation;
    const pending = (async () => {
      await callbacks.reconcileSessions();
      const current = watcher;
      if (!current?.connected) return;
      callbacks.updateAllSessions((session) => ({
        ...session,
        liveState: session.needsReplay ? session.liveState : "ready",
      }));
    })();
    reconciliation = pending;
    try {
      await pending;
    } finally {
      if (reconciliation === pending) reconciliation = null;
    }
  }

  function createWatcher(): Watcher {
    const gate = createReadinessGate();
    return {
      controller: new AbortController(),
      connected: false,
      ...gate,
    };
  }

  function resetReadiness(current: Watcher): void {
    if (!current.connected) return;
    current.connected = false;
    Object.assign(current, createReadinessGate());
  }

  function markOpen(current: Watcher, generation: string | null): void {
    if (watcher !== current || current.controller.signal.aborted) return;
    applyReady(generation);
    if (current.connected) return;
    current.connected = true;
    current.resolveReady();
  }

  async function waitForReadiness(current: Watcher): Promise<void> {
    if (current.connected) return;
    await new Promise<void>((resolve, reject) => {
      const timeout = globalThis.setTimeout(() => {
        const error = new AgentTransportError({
          code: "live_unavailable",
          message: `Agent 实时连接在 ${readinessTimeoutMs}ms 内未就绪`,
          operation: "agent_live_readiness",
          budgetMs: readinessTimeoutMs,
          status: null,
          occurredAt: new Date().toISOString(),
        });
        markDisconnected(error);
        reject(error);
      }, readinessTimeoutMs);
      current.ready.then(resolve, reject).finally(() => {
        globalThis.clearTimeout(timeout);
      });
    });
  }

  async function ensureWatcher(): Promise<void> {
    if (watcher === null) {
      watcher = createWatcher();
      callbacks.updateAllSessions((session) => ({
        ...session,
        liveState: "reconnecting",
      }));
      void runWatcher(watcher);
    }
    const current = watcher;
    while (watcher === current && !current.connected) {
      await waitForReadiness(current);
    }
    if (watcher !== current) {
      throw new AgentTransportError({
        code: "live_unavailable",
        message: "Agent 实时连接已被替换",
        operation: "agent_live_readiness",
        budgetMs: readinessTimeoutMs,
        status: null,
        occurredAt: new Date().toISOString(),
      });
    }
    callbacks.updateAllSessions((session) => ({
      ...session,
      liveState: session.needsReplay ? session.liveState : "ready",
    }));
  }

  async function runWatcher(current: Watcher): Promise<void> {
    while (!current.controller.signal.aborted) {
      let disconnectError: unknown = new AgentTransportError({
        code: "live_disconnected",
        message: "Agent 实时连接已断开，正在重新连接",
        operation: "agent_live_stream",
        budgetMs: null,
        status: null,
        occurredAt: new Date().toISOString(),
      });
      try {
        await getEventStream(agentEventsUrl(), routeEvent, {
          signal: current.controller.signal,
          onOpen: (generation) => markOpen(current, generation),
          onControl: handleControl,
        });
      } catch (error) {
        if (current.controller.signal.aborted) return;
        disconnectError = error;
      }
      if (current.controller.signal.aborted) return;
      const wasConnected = current.connected;
      resetReadiness(current);
      markDisconnected(disconnectError);
      if (wasConnected) {
        // 对账会等待本 watcher 重新 ready，不能在重连循环里反向 await 它。首次建连
        // 失败也不重复排队 snapshot；发起 watcher 的 readiness fallback 会负责首轮对账。
        void callbacks.reconcileSessions().catch(() => {
          // snapshot 失败已由 application 层保留现有状态，下一次显式 refresh 继续对账。
        });
      }
      await reconnectDelay(current.controller.signal);
    }
  }

  function markDisconnected(error: unknown): void {
    const problem = agentProblemFromUnknown(error, {
      code: "live_disconnected",
      operation: "agent_live_stream",
      budgetMs: null,
    });
    callbacks.updateAllSessions((session) => ({
      ...session,
      liveState: "reconnecting",
      needsReplay: true,
      turnState: isUnfinished(session.turnState)
        ? "unknown"
        : session.turnState,
      transportError: problem.message,
      transportProblem: problem,
    }));
  }

  function watchInBackground(): void {
    void ensureWatcher().catch(() => {
      // 错误已经写入全部 session；后台启动没有额外调用方需要接住失败。
    });
  }

  async function refreshCodexGoal(sid: string): Promise<void> {
    const session = callbacks.getSession(sid);
    if (session?.runtime !== "codex") return;
    const previousProblem =
      session.transportProblem?.operation === "goal_get"
        ? session.transportProblem
        : null;
    try {
      const goal = await getCodexGoal(sid);
      callbacks.updateSession(sid, (current) => ({
        ...current,
        goal,
        ...(previousProblem && current.transportProblem === previousProblem
          ? CLEARED_TRANSPORT_ISSUE
          : {}),
      }));
    } catch (error) {
      const problem = agentProblemFromUnknown(error, {
        code: "http_error",
        operation: "goal_get",
        budgetMs: null,
      });
      callbacks.updateSession(sid, (current) => ({
        ...current,
        ...transportIssueFromProblem(
          problem.operation === "goal_get"
            ? problem
            : { ...problem, operation: "goal_get" },
        ),
      }));
    }
  }

  function stop(sid: string): void {
    bufferedEvents.delete(sid);
    overflowedBuffers.delete(sid);
    materializing.delete(sid);
  }

  function stopAll(): void {
    const current = watcher;
    watcher = null;
    if (current) {
      current.controller.abort();
      current.rejectReady(new Error("Agent event watcher stopped"));
    }
    bufferedEvents.clear();
    overflowedBuffers.clear();
    materializing.clear();
    reconciliation = null;
    streamGeneration = null;
  }

  return {
    ensureWatcher,
    watchInBackground,
    refreshCodexGoal,
    stop,
    stopAll,
  };
}

function isUnfinished(state: PerSessionState["turnState"]): boolean {
  return (
    state === "starting" ||
    state === "running" ||
    state === "awaiting_input" ||
    state === "unknown"
  );
}

function createReadinessGate(): Pick<
  Watcher,
  "ready" | "resolveReady" | "rejectReady"
> {
  let resolveReady!: () => void;
  let rejectReady!: (reason: unknown) => void;
  const ready = new Promise<void>((resolve, reject) => {
    resolveReady = resolve;
    rejectReady = reject;
  });
  return { ready, resolveReady, rejectReady };
}

async function reconnectDelay(signal: AbortSignal): Promise<void> {
  await new Promise<void>((resolve) => {
    const finish = () => {
      window.clearTimeout(timeout);
      signal.removeEventListener("abort", finish);
      resolve();
    };
    const timeout = window.setTimeout(finish, RECONNECT_DELAY_MS);
    if (signal.aborted) finish();
    else signal.addEventListener("abort", finish, { once: true });
  });
}
