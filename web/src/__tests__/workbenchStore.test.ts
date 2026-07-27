import { beforeEach, describe, expect, it, vi } from "vitest";

import type { WorkbenchState } from "../api/modelOs";
import {
  fetchWorkbench,
  requestWorkbenchForeground,
  subscribeWorkbench,
} from "../api/modelOs";
import { useWorkbenchStore } from "../stores/workbenchStore";

vi.mock("../api/modelOs", () => ({
  fetchWorkbench: vi.fn(),
  requestWorkbenchForeground: vi.fn(),
  setWorkbenchAutomation: vi.fn(),
  setWorkbenchTaskWarm: vi.fn(),
  setWorkbenchTaskPriority: vi.fn(),
  recordWorkbenchCandidateOutcome: vi.fn(),
  sendWorkbenchInstruction: vi.fn(),
  replyWorkbenchWaiting: vi.fn(),
  subscribeWorkbench: vi.fn(() => () => undefined),
}));

const emptyState: WorkbenchState = {
  as_of: { event_seq: 0, decision_seq: 0 },
  automation_paused: false,
  foreground_task_id: null,
  next_task_id: null,
  tasks: [],
  candidates: [],
  recent_events: [],
};

beforeEach(() => {
  vi.clearAllMocks();
  useWorkbenchStore.setState({
    snapshot: null,
    loading: false,
    actionPending: null,
    error: null,
  });
});

describe("workbenchStore", () => {
  it("keeps the last authoritative state when a command is rejected", async () => {
    vi.mocked(fetchWorkbench).mockResolvedValue(emptyState);
    vi.mocked(requestWorkbenchForeground).mockRejectedValue(
      new Error("当前写入还不能安全切换"),
    );
    await useWorkbenchStore.getState().load();

    await useWorkbenchStore.getState().requestForeground("task-2");

    expect(useWorkbenchStore.getState().snapshot).toBe(emptyState);
    expect(useWorkbenchStore.getState().error).toBe(
      "当前写入还不能安全切换",
    );
  });

  it("ignores duplicate and older stream snapshots but accepts a newer boundary", async () => {
    let push: ((state: WorkbenchState) => void) | undefined;
    vi.mocked(fetchWorkbench).mockResolvedValue({
      ...emptyState,
      as_of: { event_seq: 8, decision_seq: 3 },
    });
    vi.mocked(subscribeWorkbench).mockImplementation((onState) => {
      push = onState;
      return () => undefined;
    });
    await useWorkbenchStore.getState().load();
    const loaded = useWorkbenchStore.getState().snapshot;
    useWorkbenchStore.getState().connect();

    useWorkbenchStore.setState({ error: "连接断开" });
    push?.({
      ...emptyState,
      as_of: { event_seq: 8, decision_seq: 3 },
    });
    expect(useWorkbenchStore.getState().error).toBeNull();
    expect(useWorkbenchStore.getState().snapshot).toBe(loaded);

    push?.({
      ...emptyState,
      as_of: { event_seq: 7, decision_seq: 3 },
    });
    expect(useWorkbenchStore.getState().snapshot).toBe(loaded);

    const newer = {
      ...emptyState,
      as_of: { event_seq: 8, decision_seq: 4 },
    };
    push?.(newer);
    expect(useWorkbenchStore.getState().snapshot).toBe(newer);
  });
});
