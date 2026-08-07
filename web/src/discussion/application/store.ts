/** 管理研讨列表、当前公开快照、命令状态和独立事件订阅。 */

import { create } from "zustand";
import type { AgentConnectionOption } from "../../agent/transport";
import { listAgentConnectionOptions } from "../../agent/transport";
import {
  INITIAL_DISCUSSION_DOMAIN_STATE,
  reduceDiscussion,
  type CreateDiscussionInput,
  type Discussion,
  type DiscussionDomainState,
  type DiscussionSessionConfiguration,
  type HandoffAgentInput,
  type HandoffResult,
} from "../domain";
import {
  addDiscussionMessage,
  continueDiscussion,
  createDiscussion,
  deleteDiscussion,
  finishDiscussion,
  getDiscussion,
  handoffDiscussion,
  listDiscussionSessionConfigurations,
  listDiscussions,
  markDiscussionResult,
  resumeDiscussion,
  startDiscussion,
  stopDiscussion,
  watchDiscussionEvents,
} from "../transport";

interface DiscussionState extends DiscussionDomainState {
  readonly discussions: readonly Discussion[];
  readonly connections: readonly AgentConnectionOption[];
  readonly sessionConfigurations: readonly DiscussionSessionConfiguration[];
  readonly loading: boolean;
  readonly catalogLoading: boolean;
  readonly commandPending: string | null;
  readonly error: string | null;
  load: () => Promise<void>;
  loadCatalog: () => Promise<void>;
  open: (id: string) => Promise<void>;
  createDiscussion: (input: CreateDiscussionInput) => Promise<Discussion>;
  start: () => Promise<void>;
  continueRound: (
    progressionMode?: "automatic" | "user_guided",
    additionalRounds?: number | null,
  ) => Promise<void>;
  finish: () => Promise<void>;
  stop: () => Promise<void>;
  resume: () => Promise<void>;
  remove: () => Promise<void>;
  addMessage: (body: string, targetId: string | null) => Promise<void>;
  mark: (
    roundNumber: number,
    participantId: string,
    marked: boolean,
  ) => Promise<void>;
  handoff: (agent: HandoffAgentInput, instruction: string) => Promise<HandoffResult>;
  clearError: () => void;
}

let watcher: AbortController | null = null;
const refreshPromises = new Map<string, Promise<void>>();

/** 研讨只消费公开 DTO；SSE 事件仅推进水位并触发权威快照刷新。 */
export const useDiscussionStore = create<DiscussionState>((set, get) => {
  function accept(discussion: Discussion, settleCommand = false): void {
    set((state) => ({
      ...reduceDiscussion(state, { type: "snapshot", discussion }),
      discussions: upsertSummary(state.discussions, discussion),
      loading: false,
      commandPending: settleCommand ? null : state.commandPending,
      error: null,
    }));
  }

  async function runCommand(
    name: string,
    operation: (discussion: Discussion) => Promise<Discussion>,
    propagateError = false,
  ): Promise<void> {
    const current = get().discussion;
    if (!current || get().commandPending) return;
    set({ commandPending: name, error: null });
    try {
      accept(await operation(current), true);
    } catch (error) {
      set({
        commandPending: null,
        error: error instanceof Error ? error.message : "研讨命令失败",
      });
      if (propagateError) throw error;
    }
  }

  async function refresh(id: string): Promise<void> {
    const existing = refreshPromises.get(id);
    if (existing) return existing;
    const refreshing = (async () => {
      try {
        const discussion = await getDiscussion(id);
        if (get().discussion?.id === id) accept(discussion);
      } catch (error) {
        if (get().discussion?.id === id) {
          set({ error: error instanceof Error ? error.message : "研讨刷新失败" });
        }
      } finally {
        refreshPromises.delete(id);
      }
    })();
    refreshPromises.set(id, refreshing);
    return refreshing;
  }

  function startWatcher(id: string): void {
    watcher?.abort();
    const controller = new AbortController();
    watcher = controller;
    void (async () => {
      while (!controller.signal.aborted && get().discussion?.id === id) {
        try {
          await watchDiscussionEvents(
            id,
            get().lastSequence,
            (event) => {
              set((state) => reduceDiscussion(state, { type: "event", event }));
              void refresh(id);
            },
            controller.signal,
          );
        } catch (error) {
          if (controller.signal.aborted) return;
          set({ error: error instanceof Error ? error.message : "研讨事件流断开" });
          await new Promise((resolve) => globalThis.setTimeout(resolve, 800));
        }
      }
    })();
  }

  return {
    ...INITIAL_DISCUSSION_DOMAIN_STATE,
    discussions: [],
    connections: [],
    sessionConfigurations: [],
    loading: false,
    catalogLoading: false,
    commandPending: null,
    error: null,

    load: async () => {
      set({ loading: true, error: null });
      try {
        const discussions = await listDiscussions();
        set({ discussions, loading: false });
        const current = get().discussion;
        if (!current && discussions[0]) await get().open(discussions[0].id);
      } catch (error) {
        set({
          loading: false,
          error: error instanceof Error ? error.message : "研讨历史读取失败",
        });
      }
    },

    loadCatalog: async () => {
      set({ catalogLoading: true, error: null });
      try {
        const [connections, sessionConfigurations] = await Promise.all([
          listAgentConnectionOptions(),
          listDiscussionSessionConfigurations(),
        ]);
        set({ connections, sessionConfigurations, catalogLoading: false });
      } catch (error) {
        set({
          catalogLoading: false,
          error: error instanceof Error ? error.message : "会话配置读取失败",
        });
      }
    },

    open: async (id) => {
      watcher?.abort();
      set({
        ...INITIAL_DISCUSSION_DOMAIN_STATE,
        loading: true,
        error: null,
      });
      try {
        const discussion = await getDiscussion(id);
        accept(discussion, true);
        startWatcher(id);
      } catch (error) {
        set({
          loading: false,
          error: error instanceof Error ? error.message : "研讨读取失败",
        });
      }
    },

    createDiscussion: async (input) => {
      set({ commandPending: "create", error: null });
      try {
        const discussion = await createDiscussion(input);
        accept(discussion, true);
        startWatcher(discussion.id);
        return discussion;
      } catch (error) {
        set({
          commandPending: null,
          error: error instanceof Error ? error.message : "研讨创建失败",
        });
        throw error;
      }
    },

    start: () =>
      runCommand("start", (discussion) =>
        startDiscussion(discussion.id, discussion.version),
      ),
    continueRound: (progressionMode = "user_guided", additionalRounds = null) =>
      runCommand("continue", (discussion) =>
        continueDiscussion(
          discussion.id,
          discussion.version,
          progressionMode,
          additionalRounds,
        ),
      ),
    finish: () =>
      runCommand("finish", (discussion) =>
        finishDiscussion(discussion.id, discussion.version),
      ),
    stop: () =>
      runCommand("stop", (discussion) =>
        stopDiscussion(discussion.id, discussion.version),
      ),
    resume: () =>
      runCommand("resume", (discussion) =>
        resumeDiscussion(discussion.id, discussion.version),
      ),
    remove: async () => {
      const discussion = get().discussion;
      if (!discussion || get().commandPending) return;
      set({ commandPending: "delete", error: null });
      try {
        await deleteDiscussion(discussion.id, discussion.version);
        watcher?.abort();
        const remaining = get().discussions.filter((item) => item.id !== discussion.id);
        set({
          discussions: remaining,
          ...INITIAL_DISCUSSION_DOMAIN_STATE,
          commandPending: null,
        });
        if (remaining[0]) await get().open(remaining[0].id);
      } catch (error) {
        set({
          commandPending: null,
          error: error instanceof Error ? error.message : "研讨删除失败",
        });
      }
    },
    addMessage: (body, targetId) =>
      runCommand(
        "message",
        (discussion) =>
          addDiscussionMessage(
            discussion.id,
            discussion.version,
            body,
            targetId,
          ),
        true,
      ),
    mark: (roundNumber, participantId, marked) =>
      runCommand("mark", (discussion) =>
        markDiscussionResult(
          discussion.id,
          discussion.version,
          roundNumber,
          participantId,
          marked,
        ),
      ),
    handoff: async (agent, instruction) => {
      const discussion = get().discussion;
      if (!discussion) throw new Error("尚未选择研讨");
      set({ commandPending: "handoff", error: null });
      try {
        const result = await handoffDiscussion(
          discussion.id,
          discussion.version,
          agent,
          instruction,
        );
        await refresh(discussion.id);
        set({ commandPending: null });
        return result;
      } catch (error) {
        set({
          commandPending: null,
          error: error instanceof Error ? error.message : "Agent 交接失败",
        });
        throw error;
      }
    },
    clearError: () => set({ error: null }),
  };
});

function upsertSummary(
  discussions: readonly Discussion[],
  current: Discussion,
): readonly Discussion[] {
  return [current, ...discussions.filter((item) => item.id !== current.id)].sort(
    (left, right) => right.updated_at.localeCompare(left.updated_at),
  );
}
