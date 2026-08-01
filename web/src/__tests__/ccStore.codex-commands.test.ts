import { describe, expect, it, vi } from "vitest";

import {
  apiCompactCodexSession,
  apiStartCodexReview,
  ev,
  mockCreate,
  releaseAllStreams,
  stream,
} from "./ccStoreTestHarness";
import { createAgentStore } from "../agent";

describe("createAgentStore - Codex native commands", () => {
  it("runs compact without creating an optimistic user turn", async () => {
    const store = createAgentStore();
    mockCreate("c1", { runtime: "codex", native_session_id: "thread-1" });
    await store.getState().startSession({ workdir: "/a", runtime: "codex" });

    await store.getState().compactCodex();

    expect(apiCompactCodexSession).toHaveBeenCalledWith("c1");
    expect(store.getState().sessions.c1.turns).toHaveLength(0);
    expect(store.getState().sessions.c1.commandPending).toBe("compact");
    expect(store.getState().sessions.c1.phase).toBe("compacting");
    stream.apply!(
      ev(
        "turn_start",
        { autonomous: true, revertible: false },
        { runtime: "codex", session_id: "c1", turn_id: "compact-turn-1" },
      ),
    );
    expect(store.getState().sessions.c1.commandPending).toBeNull();
    expect(store.getState().sessions.c1.phase).toBe("compacting");
  });

  it("releases compact UI admission when the Codex host exits before turn start", async () => {
    const store = createAgentStore();
    mockCreate("c1", { runtime: "codex", native_session_id: "thread-1" });
    await store.getState().startSession({ workdir: "/a", runtime: "codex" });
    await store.getState().compactCodex();

    stream.apply!(
      ev("host_status", { status: "host_exited" }, { runtime: "codex", session_id: "c1" }),
    );

    expect(store.getState().sessions.c1.commandPending).toBeNull();
    expect(store.getState().sessions.c1.abort).toBeNull();
  });

  it("releases compact UI admission when its event stream closes", async () => {
    const store = createAgentStore();
    mockCreate("c1", { runtime: "codex", native_session_id: "thread-1" });
    await store.getState().startSession({ workdir: "/a", runtime: "codex" });
    await store.getState().compactCodex();

    await releaseAllStreams();

    await vi.waitFor(() => {
      expect(store.getState().sessions.c1.commandPending).toBeNull();
    });
  });

  it("starts review as an autonomous turn without a user message", async () => {
    const store = createAgentStore();
    mockCreate("c1", { runtime: "codex", native_session_id: "thread-1" });
    await store.getState().startSession({ workdir: "/a", runtime: "codex" });

    await store.getState().startCodexReview({
      type: "baseBranch",
      branch: "main",
    });

    expect(apiStartCodexReview).toHaveBeenCalledWith("c1", {
      type: "baseBranch",
      branch: "main",
    });
    expect(store.getState().sessions.c1.turns).toHaveLength(1);
    expect(store.getState().sessions.c1.turns[0]).toMatchObject({
      userText: "",
      turnId: "review-turn-1",
      status: "active",
    });
    expect(store.getState().sessions.c1.abort).not.toBeNull();
  });

  it("does not revive a review that finished before the start response", async () => {
    const store = createAgentStore();
    let resolveReview!: (result: {
      reviewThreadId: string;
      turnId: string;
    }) => void;
    apiStartCodexReview.mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          resolveReview = resolve;
        }),
    );
    mockCreate("c1", { runtime: "codex", native_session_id: "thread-1" });
    await store.getState().startSession({ workdir: "/a", runtime: "codex" });

    const starting = store
      .getState()
      .startCodexReview({ type: "uncommittedChanges" });
    await vi.waitFor(() => expect(apiStartCodexReview).toHaveBeenCalled());
    stream.apply!(
      ev(
        "turn_start",
        { autonomous: true, revertible: false },
        { runtime: "codex", session_id: "c1", turn_id: "review-turn-1" },
      ),
    );
    stream.apply!(
      ev(
        "finished",
        {},
        { runtime: "codex", session_id: "c1", turn_id: "review-turn-1" },
      ),
    );
    resolveReview({ reviewThreadId: "thread-1", turnId: "review-turn-1" });
    await starting;

    expect(store.getState().sessions.c1.turns).toHaveLength(1);
    expect(store.getState().sessions.c1.turns[0]).toMatchObject({
      turnId: "review-turn-1",
      status: "done",
    });
    expect(store.getState().sessions.c1.abort).toBeNull();
  });

  it("records command failure on the originating session", async () => {
    const store = createAgentStore();
    mockCreate("c1", {
      runtime: "codex",
      native_session_id: "thread-1",
      connected: true,
    });
    await store.getState().startSession({ workdir: "/a", runtime: "codex" });
    apiCompactCodexSession.mockRejectedValueOnce(new Error("compact failed"));

    await expect(store.getState().compactCodex()).rejects.toThrow("compact failed");

    expect(store.getState().sessions.c1.transportError).toBe("compact failed");
    expect(store.getState().sessions.c1.commandPending).toBeNull();
    expect(store.getState().sessions.c1.phase).toBe("idle");
  });
});
