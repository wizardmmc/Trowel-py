import { describe, expect, it, vi } from "vitest";

import {
  apiClearCodexGoal,
  apiGetCodexGoal,
  apiGetEventStream,
  apiSetCodexGoal,
  apiStartCodexTurn,
  ev,
  listActiveSessions,
  mockCreate,
  stream,
} from "./ccStoreTestHarness";
import { createAgentStore } from "../agent";

const GOAL = {
  objective: "Ship Goal and Plan",
  status: "active" as const,
  tokenBudget: 12000,
  tokensUsed: 7448,
  timeUsedSeconds: 9,
  createdAt: 10,
  updatedAt: 11,
};

describe("createAgentStore - Codex Goal and Plan", () => {
  it("does not start a fresh Codex thread before the first user message", async () => {
    const store = createAgentStore();
    mockCreate("draft", { runtime: "codex", native_session_id: null });

    await store.getState().startSession({ workdir: "/draft", runtime: "codex" });
    await store.getState().activateSession("draft");

    expect(apiGetEventStream).not.toHaveBeenCalled();
    expect(apiGetCodexGoal).not.toHaveBeenCalled();
  });

  it("loads Goal on session start and keeps it isolated per session", async () => {
    const store = createAgentStore();
    apiGetCodexGoal.mockResolvedValueOnce(GOAL).mockResolvedValueOnce(null);
    mockCreate("c1", { runtime: "codex", native_session_id: "thread-1" });
    await store.getState().startSession({ workdir: "/a", runtime: "codex" });
    await store.getState().send("keep c1");
    stream.apply!(
      ev("finished", {}, { runtime: "codex", session_id: "c1" }),
    );
    mockCreate("c2", { runtime: "codex", native_session_id: "thread-2" });
    await store.getState().startSession({ workdir: "/b", runtime: "codex" });

    expect(store.getState().sessions.c1.goal).toEqual(GOAL);
    expect(store.getState().sessions.c2.goal).toBeNull();
  });

  it("restores watchers only for Codex sessions live in this backend process", async () => {
    const store = createAgentStore();
    const disconnected = {
      session_id: "old",
      runtime: "codex",
      native_session_id: "thread-old",
      workdir: "/old",
      model: "gpt-5.6-sol",
      effort: "high",
      permission: null,
      memory_enabled: true,
      profile_enabled: true,
      capabilities: ["tools", "approval"],
      name: "old",
      connected: false,
      running: false,
    } as const;
    const connected = {
      ...disconnected,
      session_id: "live",
      native_session_id: "thread-live",
      name: "live",
      connected: true,
    };
    const unmaterialized = {
      ...connected,
      session_id: "draft",
      native_session_id: null,
      name: "draft",
    };
    listActiveSessions.mockResolvedValueOnce({
      sessions: [disconnected, connected, unmaterialized],
      activeId: "live",
    });

    await store.getState().refreshActiveSessions();

    expect(apiGetCodexGoal).toHaveBeenCalledTimes(1);
    expect(apiGetCodexGoal).toHaveBeenCalledWith("live");
  });

  it("records a background watcher startup failure without rejecting session start", async () => {
    const store = createAgentStore();
    apiGetEventStream.mockRejectedValueOnce(new Error("watcher unavailable"));
    mockCreate("c1", { runtime: "codex", native_session_id: "thread-1" });

    await expect(
      store.getState().startSession({ workdir: "/a", runtime: "codex" }),
    ).resolves.toBeDefined();
    await vi.waitFor(() => {
      expect(store.getState().sessions.c1.transportError).toBe(
        "watcher unavailable",
      );
    });
  });

  it("uses the permanent watcher for autonomous turns and explicit sends", async () => {
    const store = createAgentStore();
    mockCreate("c1", { runtime: "codex", native_session_id: "thread-1" });
    await store.getState().startSession({ workdir: "/a", runtime: "codex" });

    const sending = store.getState().send("continue");
    await sending;
    expect(apiStartCodexTurn).toHaveBeenCalledWith("c1", "continue");
    expect(store.getState().sessions.c1.abort).not.toBeNull();

    stream.apply!(
      ev(
        "finished",
        {},
        { runtime: "codex", session_id: "c1", turn_id: "turn-1" },
      ),
    );
    expect(store.getState().sessions.c1.abort).toBeNull();

    stream.apply!(
      ev(
        "turn_start",
        { revertible: false, autonomous: true },
        { runtime: "codex", session_id: "c1", turn_id: "turn-auto" },
      ),
    );
    expect(store.getState().sessions.c1.turns.at(-1)?.turnId).toBe("turn-auto");
    expect(store.getState().sessions.c1.abort).not.toBeNull();
  });

  it("writes pause and clear through native Goal APIs", async () => {
    const store = createAgentStore();
    apiGetCodexGoal.mockResolvedValueOnce(GOAL);
    apiSetCodexGoal.mockResolvedValueOnce({ ...GOAL, status: "paused" });
    mockCreate("c1", { runtime: "codex", native_session_id: "thread-1" });
    await store.getState().startSession({ workdir: "/a", runtime: "codex" });

    await store.getState().setCodexGoal({ status: "paused" });
    expect(apiSetCodexGoal).toHaveBeenCalledWith("c1", { status: "paused" });
    expect(store.getState().sessions.c1.goal?.status).toBe("paused");

    await store.getState().clearCodexGoal();
    expect(apiClearCodexGoal).toHaveBeenCalledWith("c1");
    expect(store.getState().sessions.c1.goal).toBeNull();
  });
});
