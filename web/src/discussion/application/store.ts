/** 管理研讨列表、当前公开快照、命令状态和独立事件订阅。 */

import { create } from "zustand";
import type { AgentConnectionOption } from "../../agent/transport";
import { listAgentConnectionOptions } from "../../agent/transport";
import {
  applyAttemptEvent,
  createLiveAttemptTimeline,
  INITIAL_DISCUSSION_DOMAIN_STATE,
  replayAttemptTimeline,
  reduceDiscussion,
  type CreateDiscussionInput,
  type Discussion,
  type DiscussionDomainState,
  type DiscussionSessionConfiguration,
  type DiscussionAttemptTimeline,
  type HandoffAgentInput,
  type HandoffResult,
} from "../domain";
import {
  addDiscussionMessage,
  answerDiscussionParticipantQuestion,
  continueDiscussion,
  createDiscussion,
  deleteDiscussion,
  finishDiscussion,
  getDiscussion,
  getDiscussionAttemptEvents,
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
  readonly attemptTimelines: Readonly<Record<string, DiscussionAttemptTimeline>>;
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
  answerQuestion: (
    participantId: string,
    attemptId: string,
    requestId: string,
    answers: Readonly<Record<string, string>>,
  ) => Promise<void>;
  handoff: (agent: HandoffAgentInput, instruction: string) => Promise<HandoffResult>;
  clearError: () => void;
}

let watcher: AbortController | null = null;
let openGeneration = 0;
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
    void ensureAttemptTimelines(discussion);
  }

  async function loadAttemptTimeline(
    discussionId: string,
    attemptId: string,
    participantId: string,
    roundNumber: number,
    runtime: "claude_code" | "codex",
  ): Promise<void> {
    const current = get().attemptTimelines[attemptId];
    if (current?.availability === "loading") return;
    const base =
      current ??
      createLiveAttemptTimeline(attemptId, participantId, roundNumber, runtime);
    const startedAtSeq = base.lastSeq;
    set((state) => ({
      attemptTimelines: {
        ...state.attemptTimelines,
        [attemptId]: { ...base, availability: "loading" },
      },
    }));
    try {
      const history = await getDiscussionAttemptEvents(discussionId, attemptId);
      set((state) => {
        const latest = state.attemptTimelines[attemptId] ?? base;
        if (latest.lastSeq !== startedAtSeq) {
          return {
            attemptTimelines: {
              ...state.attemptTimelines,
              [attemptId]: {
                ...latest,
                needsReplay: true,
                availability: "live",
              },
            },
          };
        }
        if (history.availability === "unavailable") {
          return {
            attemptTimelines: {
              ...state.attemptTimelines,
              [attemptId]: {
                ...latest,
                needsReplay: true,
                availability: "unavailable",
              },
            },
          };
        }
        return {
          attemptTimelines: {
            ...state.attemptTimelines,
            [attemptId]: replayAttemptTimeline(
              { ...latest, runtime: history.runtime },
              history.events,
              history.status,
            ),
          },
        };
      });
    } catch {
      set((state) => {
        const latest = state.attemptTimelines[attemptId] ?? base;
        return {
          attemptTimelines: {
            ...state.attemptTimelines,
            [attemptId]: { ...latest, availability: "unavailable" },
          },
        };
      });
    }
  }

  async function ensureAttemptTimelines(discussion: Discussion): Promise<void> {
    const participantRuntime = new Map(
      discussion.participants.map((item) => [item.id, item.runtime] as const),
    );
    const loads: Promise<void>[] = [];
    for (const round of discussion.rounds) {
      for (const slot of round.participants) {
        const attemptId = slot.current_attempt_id;
        const runtime = participantRuntime.get(slot.participant_id);
        if (!attemptId || !runtime) continue;
        const timeline = get().attemptTimelines[attemptId];
        const terminal = !["pending", "running", "needs_reconcile"].includes(
          slot.status,
        );
        const shouldLoad = terminal
          ? !timeline || timeline.needsReplay
          : !timeline;
        if (!shouldLoad) continue;
        loads.push(
          loadAttemptTimeline(
            discussion.id,
            attemptId,
            slot.participant_id,
            round.number,
            runtime,
          ),
        );
      }
    }
    await Promise.all(loads);
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
      let requestedSequence = get().lastSequence;
      try {
        // 刷新期间到达的新持久事件必须补读一次，否则相邻参与者完成会被合并丢失。
        while (true) {
          const discussion = await getDiscussion(id);
          if (get().discussion?.id !== id) break;
          accept(discussion);
          const latestSequence = get().lastSequence;
          if (latestSequence <= requestedSequence) break;
          requestedSequence = latestSequence;
        }
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
              if (event.type === "attempt_event" && "event" in event) {
                set((state) => {
                  const current =
                    state.attemptTimelines[event.attempt_id] ??
                    createLiveAttemptTimeline(
                      event.attempt_id,
                      event.participant_id,
                      event.round_number,
                      event.event.runtime,
                    );
                  return {
                    attemptTimelines: {
                      ...state.attemptTimelines,
                      [event.attempt_id]: applyAttemptEvent(
                        current,
                        event.event,
                        event.attempt_sequence,
                      ),
                    },
                  };
                });
                return;
              }
              if (event.type === "attempt_gap" && "attempt_id" in event) {
                const discussion = get().discussion;
                const runtime = discussion?.participants.find(
                  (item) => item.id === event.participant_id,
                )?.runtime;
                if (discussion && runtime) {
                  set((state) => {
                    const current =
                      state.attemptTimelines[event.attempt_id] ??
                      createLiveAttemptTimeline(
                        event.attempt_id,
                        event.participant_id,
                        event.round_number,
                        runtime,
                      );
                    return {
                      attemptTimelines: {
                        ...state.attemptTimelines,
                        [event.attempt_id]: { ...current, needsReplay: true },
                      },
                    };
                  });
                  void loadAttemptTimeline(
                    id,
                    event.attempt_id,
                    event.participant_id,
                    event.round_number,
                    runtime,
                  );
                }
                return;
              }
              if (!("sequence" in event)) return;
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
    attemptTimelines: {},

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
      const generation = ++openGeneration;
      set({ loading: true, error: null });
      try {
        const discussion = await getDiscussion(id);
        if (generation !== openGeneration) return;
        accept(discussion, true);
        startWatcher(id);
      } catch (error) {
        if (generation !== openGeneration) return;
        set({
          loading: false,
          error: error instanceof Error ? error.message : "研讨读取失败",
        });
      }
    },

    createDiscussion: async (input) => {
      openGeneration += 1;
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
        openGeneration += 1;
        watcher?.abort();
        const remaining = get().discussions.filter((item) => item.id !== discussion.id);
        set({
          discussions: remaining,
          ...INITIAL_DISCUSSION_DOMAIN_STATE,
          attemptTimelines: {},
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
    answerQuestion: async (
      participantId,
      attemptId,
      requestId,
      answers,
    ) => {
      const discussion = get().discussion;
      if (!discussion) throw new Error("尚未选择研讨");
      try {
        await answerDiscussionParticipantQuestion(
          discussion.id,
          participantId,
          attemptId,
          requestId,
          answers,
        );
      } catch (error) {
        set({
          error: error instanceof Error ? error.message : "参与者提问回答失败",
        });
        throw error;
      }
    },
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
