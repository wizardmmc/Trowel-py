import { create } from "zustand";

import {
  fetchWorkbench,
  recordWorkbenchCandidateOutcome,
  requestWorkbenchForeground,
  replyWorkbenchWaiting,
  sendWorkbenchInstruction,
  setWorkbenchAutomation,
  setWorkbenchTaskPriority,
  setWorkbenchTaskWarm,
  subscribeWorkbench,
  type WorkbenchCandidate,
  type WorkbenchState,
} from "../api/modelOs";

interface WorkbenchStore {
  readonly snapshot: WorkbenchState | null;
  readonly loading: boolean;
  readonly actionPending: string | null;
  readonly error: string | null;
  load(): Promise<void>;
  connect(): () => void;
  setAutomation(paused: boolean): Promise<void>;
  toggleWarm(taskId: string, warm: boolean): Promise<void>;
  setPriority(taskId: string, priority: number): Promise<void>;
  requestForeground(taskId: string): Promise<void>;
  recordCandidateOutcome(
    sourceKind: WorkbenchCandidate["source_kind"],
    candidateId: string,
    outcome: "adopted" | "dismissed" | "invalid",
    reason?: string,
  ): Promise<void>;
  sendInstruction(taskId: string, text: string): Promise<void>;
  replyWaiting(
    taskId: string,
    correlationId: string,
    reply:
      | { readonly answers: Readonly<Record<string, string>> }
      | { readonly decision: string },
  ): Promise<void>;
  clearError(): void;
}

function messageFrom(error: unknown): string {
  return error instanceof Error ? error.message : "工作台操作失败";
}

function isNewer(
  current: WorkbenchState | null,
  incoming: WorkbenchState,
): boolean {
  if (current === null) return true;
  const currentBoundary = current.as_of;
  const incomingBoundary = incoming.as_of;
  return (
    incomingBoundary.event_seq >= currentBoundary.event_seq &&
    incomingBoundary.decision_seq >= currentBoundary.decision_seq &&
    (incomingBoundary.event_seq > currentBoundary.event_seq ||
      incomingBoundary.decision_seq > currentBoundary.decision_seq)
  );
}

async function runAction(
  set: (partial: Partial<WorkbenchStore>) => void,
  key: string,
  action: () => Promise<void>,
): Promise<boolean> {
  set({ actionPending: key, error: null });
  try {
    await action();
    return true;
  } catch (error) {
    set({ error: messageFrom(error) });
    return false;
  } finally {
    set({ actionPending: null });
  }
}

export const useWorkbenchStore = create<WorkbenchStore>((set, get) => ({
  snapshot: null,
  loading: false,
  actionPending: null,
  error: null,

  async load() {
    set({ loading: get().snapshot === null, error: null });
    try {
      const snapshot = await fetchWorkbench();
      set({ snapshot, loading: false });
    } catch (error) {
      set({ loading: false, error: messageFrom(error) });
    }
  },

  connect() {
    return subscribeWorkbench(
      (snapshot) => {
        if (isNewer(get().snapshot, snapshot)) {
          set({ snapshot, error: null });
        } else if (get().error !== null) {
          set({ error: null });
        }
      },
      (error) => set({ error }),
    );
  },

  async setAutomation(paused) {
    await runAction(set, "automation", async () => {
      const snapshot = await setWorkbenchAutomation(paused);
      set({ snapshot });
    });
  },

  async toggleWarm(taskId, warm) {
    await runAction(set, `warm:${taskId}`, async () => {
      const snapshot = await setWorkbenchTaskWarm(taskId, warm);
      set({ snapshot });
    });
  },

  async setPriority(taskId, priority) {
    const success = await runAction(set, `priority:${taskId}`, async () => {
      await setWorkbenchTaskPriority(taskId, priority);
    });
    if (success) await get().load();
  },

  async requestForeground(taskId) {
    const success = await runAction(set, `foreground:${taskId}`, async () => {
      await requestWorkbenchForeground(taskId);
    });
    if (success) await get().load();
  },

  async recordCandidateOutcome(sourceKind, candidateId, outcome, reason) {
    const success = await runAction(set, `candidate:${candidateId}`, async () => {
      if (reason === undefined) {
        await recordWorkbenchCandidateOutcome(sourceKind, candidateId, outcome);
      } else {
        await recordWorkbenchCandidateOutcome(
          sourceKind,
          candidateId,
          outcome,
          reason,
        );
      }
    });
    if (success) await get().load();
  },

  async sendInstruction(taskId, text) {
    const success = await runAction(set, `instruction:${taskId}`, async () => {
      await sendWorkbenchInstruction(taskId, text);
    });
    if (success) await get().load();
  },

  async replyWaiting(taskId, correlationId, reply) {
    const success = await runAction(set, `reply:${taskId}`, async () => {
      await replyWorkbenchWaiting(taskId, correlationId, reply);
    });
    if (success) await get().load();
  },

  clearError() {
    set({ error: null });
  },
}));
