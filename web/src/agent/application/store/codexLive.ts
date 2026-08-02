/** 管理各 Codex 会话的 SSE watcher、终态收口和 goal 刷新。 */

import {
  agentEventsUrl,
  getCodexGoal,
} from "../../transport/api";
import type { AgentEvent } from "../../transport/agentEvent";
import { getEventStream } from "../../transport/stream";
import { endActiveTurnOnStreamClose } from "../../domain/reducer";
import type { PerSessionState } from "./sessionState";

interface CodexLiveCallbacks {
  readonly getSession: (sid: string) => PerSessionState | undefined;
  readonly applyEvent: (sid: string, event: AgentEvent) => void;
  readonly updateSession: (
    sid: string,
    update: (session: PerSessionState) => PerSessionState,
  ) => void;
}

interface Watcher {
  readonly controller: AbortController;
  readonly ready: Promise<void>;
  readonly rejectReady: (reason: unknown) => void;
}

export function createCodexLiveController(callbacks: CodexLiveCallbacks) {
  const watchers = new Map<string, Watcher>();

  function applyLiveEvent(sid: string, event: AgentEvent): void {
    callbacks.applyEvent(sid, event);
    const terminal =
      event.type === "finished" ||
      event.type === "interrupted" ||
      event.type === "error" ||
      (event.type === "host_status" && event.payload.status === "host_exited");
    const autonomousStart =
      event.type === "turn_start" && event.payload.autonomous === true;
    if (!terminal && !autonomousStart) return;
    callbacks.updateSession(sid, (session) => ({
      ...session,
      abort: terminal
        ? null
        : session.abort ?? new AbortController(),
      commandPending:
        terminal || autonomousStart ? null : session.commandPending,
      connected: true,
    }));
  }

  function ensureWatcher(sid: string): Promise<void> {
    const existing = watchers.get(sid);
    if (existing) return existing.ready;
    const session = callbacks.getSession(sid);
    if (!session || session.runtime !== "codex") return Promise.resolve();

    const controller = new AbortController();
    let opened = false;
    let resolveReady!: () => void;
    let rejectReady!: (reason: unknown) => void;
    const ready = new Promise<void>((resolve, reject) => {
      resolveReady = resolve;
      rejectReady = reject;
    });
    watchers.set(sid, { controller, ready, rejectReady });
    callbacks.updateSession(sid, (current) => ({
      ...current,
      plan: null,
      lastSeq: null,
      needsReplay: false,
    }));

    void getEventStream(
      agentEventsUrl(sid),
      (event) => applyLiveEvent(sid, event),
      {
        signal: controller.signal,
        onOpen: () => {
          opened = true;
          resolveReady();
        },
      },
    )
      .then(() => {
        if (controller.signal.aborted) return;
        callbacks.updateSession(sid, (current) => {
          const closed = endActiveTurnOnStreamClose(current, {
            aborted: false,
            transportOk: true,
          });
          return {
            ...current,
            ...closed,
            abort: null,
            commandPending: null,
            plan: null,
            needsReplay: true,
          };
        });
      })
      .catch((error: unknown) => {
        if (!opened) rejectReady(error);
        if (controller.signal.aborted) return;
        callbacks.updateSession(sid, (current) => ({
          ...current,
          commandPending: null,
          plan: null,
          needsReplay: true,
          transportError: errorMessage(error),
        }));
      })
      .finally(() => {
        const current = watchers.get(sid);
        if (current?.controller === controller) watchers.delete(sid);
      });
    return ready;
  }

  function watchInBackground(sid: string): void {
    void ensureWatcher(sid).catch(() => {
      // 连接错误已经写入 session；后台恢复没有额外调用者需要接住失败。
    });
  }

  async function refreshGoal(sid: string): Promise<void> {
    try {
      const goal = await getCodexGoal(sid);
      callbacks.updateSession(sid, (session) => ({ ...session, goal }));
    } catch (error) {
      callbacks.updateSession(sid, (session) => ({
        ...session,
        transportError: errorMessage(error),
      }));
    }
  }

  function stop(sid: string): void {
    const watcher = watchers.get(sid);
    if (!watcher) return;
    watcher.controller.abort();
    watcher.rejectReady(new Error("Codex event watcher stopped"));
    watchers.delete(sid);
  }

  function stopAll(): void {
    for (const sid of watchers.keys()) stop(sid);
  }

  return { ensureWatcher, watchInBackground, refreshGoal, stop, stopAll };
}

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}
