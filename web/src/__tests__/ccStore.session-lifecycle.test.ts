import { describe, expect, it, vi } from "vitest";
import {
  apiCreateSession,
  apiDeleteSession,
  apiGenerateSessionTitle,
  ev,
  mockCreate,
  releaseAllStreams,
  releaseMessageStreams,
  stream,
} from "./ccStoreTestHarness";
import { createAgentStore } from "../agent";

describe("createAgentStore — multi-session lifecycle", () => {
  it("startSession creates a session that is NOT yet connected (not in the bar)", async () => {
    const store = createAgentStore();
    mockCreate("s1");
    await store.getState().startSession({ workdir: "/wd" });
    const state = store.getState();
    expect(state.activeSid).toBe("s1");
    expect(state.sessions.s1).toBeDefined();
    expect(state.sessions.s1.connected).toBe(false);
  });

  it("send() flips the session to connected (enters the bar)", async () => {
    const store = createAgentStore();
    mockCreate("s1");
    await store.getState().startSession({ workdir: "/wd" });
    expect(store.getState().sessions.s1.connected).toBe(false);
    const sending = store.getState().send("hi");
    expect(store.getState().sessions.s1.connected).toBe(true);
    stream.apply!(ev("finished"));
    await releaseAllStreams();
    await sending;
  });

  it("keeps the Claude event watcher alive for an autonomous follow-up turn", async () => {
    const store = createAgentStore();
    mockCreate("s1");
    await store.getState().startSession({ workdir: "/wd" });

    const sending = store.getState().send("start delegate");
    expect(stream.eventApply).not.toBeNull();
    expect(stream.messageApply).not.toBeNull();
    stream.messageApply!(ev("finished"));
    await releaseMessageStreams();
    await sending;

    stream.eventApply!(
      ev(
        "turn_start",
        { autonomous: true },
        { turn_id: "turn-auto" },
      ),
    );
    expect(store.getState().sessions.s1.turns.at(-1)?.turnId).toBe("turn-auto");
    expect(store.getState().sessions.s1.abort).not.toBeNull();

    stream.eventApply!(ev("finished", {}, { turn_id: "turn-auto" }));
    expect(store.getState().sessions.s1.abort).toBeNull();
  });

  it("shows the first prompt immediately, then applies the generated title", async () => {
    const store = createAgentStore();
    const created = mockCreate("s1", {
      name: "wd #2",
      display_title: "",
      title_source: "new",
    });
    await store.getState().startSession({ workdir: "/wd" });
    let resolveTitle!: (session: ReturnType<typeof mockCreate>) => void;
    apiGenerateSessionTitle.mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          resolveTitle = resolve;
        }),
    );

    const sending = store.getState().send("  实现   同目录\n聚合展示  ");
    expect(store.getState().sessions.s1.displayTitle).toBe("实现 同目录 聚合展示");
    expect(store.getState().sessions.s1.titleSource).toBe("prompt");

    resolveTitle({
      ...created,
      display_title: "实现会话目录分组",
      title_source: "generated",
    });
    await Promise.resolve();
    await Promise.resolve();
    expect(store.getState().sessions.s1.displayTitle).toBe("实现会话目录分组");

    stream.apply!(ev("finished"));
    await releaseAllStreams();
    await sending;
  });

  it("does not let a stale generated response overwrite a manual title", async () => {
    const store = createAgentStore();
    const created = mockCreate("s1", {
      display_title: "",
      title_source: "new",
    });
    await store.getState().startSession({ workdir: "/wd" });
    let resolveTitle!: (session: ReturnType<typeof mockCreate>) => void;
    apiGenerateSessionTitle.mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          resolveTitle = resolve;
        }),
    );

    const sending = store.getState().send("第一条真实提示词");
    await store.getState().renameSessionTitle("s1", "手动保留的标题");
    resolveTitle({
      ...created,
      display_title: "迟到的自动标题",
      title_source: "generated",
    });
    await Promise.resolve();
    await Promise.resolve();

    expect(store.getState().sessions.s1.displayTitle).toBe("手动保留的标题");
    expect(store.getState().sessions.s1.titleSource).toBe("manual");

    stream.apply!(ev("finished"));
    await releaseAllStreams();
    await sending;
  });

  it("two CONNECTED sessions coexist; switching preserves both", async () => {
    const store = createAgentStore();
    mockCreate("s1");
    await store.getState().startSession({ workdir: "/a" });
    const first = store.getState().send("one");
    stream.apply!(ev("finished"));
    await releaseAllStreams();
    await first;
    mockCreate("s2");
    await store.getState().startSession({ workdir: "/b" });
    const second = store.getState().send("two");
    stream.apply!(ev("finished"));
    await releaseAllStreams();
    await second;
    expect(store.getState().sessions.s1.connected).toBe(true);
    expect(store.getState().sessions.s2.connected).toBe(true);
    expect(store.getState().activeSid).toBe("s2");

    await store.getState().activateSession("s1");
    expect(store.getState().activeSid).toBe("s1");
    expect(store.getState().sessions.s2).toBeDefined();
  });

  it("switching away from a never-connected temp drops it (切走即丢)", async () => {
    const store = createAgentStore();
    mockCreate("s1");
    await store.getState().startSession({ workdir: "/a" });
    const first = store.getState().send("one");
    stream.apply!(ev("finished"));
    await releaseAllStreams();
    await first;
    mockCreate("s2");
    await store.getState().startSession({ workdir: "/b" });
    expect(store.getState().activeSid).toBe("s2");
    await store.getState().activateSession("s1");
    expect(store.getState().sessions.s2).toBeUndefined();
    expect(store.getState().sessions.s1).toBeDefined();
    expect(apiDeleteSession).toHaveBeenCalledWith("s2");
  });

  it("keeps a temp visible when the backend cannot finish closing it", async () => {
    const store = createAgentStore();
    mockCreate("s1");
    await store.getState().startSession({ workdir: "/a" });
    const first = store.getState().send("one");
    stream.apply!(ev("finished"));
    await releaseAllStreams();
    await first;
    mockCreate("s2", { runtime: "codex" });
    await store.getState().startSession({ workdir: "/b", runtime: "codex" });
    apiDeleteSession.mockResolvedValueOnce({
      closed: false,
      status: "needs_reconcile",
      remaining_resource_count: 2,
      remaining_resource_kinds: ["codex_session_close"],
      error: "Codex close needs reconciliation",
    });

    await store.getState().activateSession("s1");

    expect(store.getState().activeSid).toBe("s1");
    expect(store.getState().sessions.s2).toMatchObject({
      connected: true,
      transportError: "Codex close needs reconciliation",
    });
  });

  it("keeps a failed temp close visible after returning to workspace home", async () => {
    const store = createAgentStore();
    mockCreate("s1", { runtime: "codex" });
    await store.getState().startSession({ workdir: "/a", runtime: "codex" });
    apiDeleteSession.mockResolvedValueOnce({
      closed: false,
      status: "needs_reconcile",
      remaining_resource_count: 1,
      remaining_resource_kinds: ["codex_session_close"],
      error: "empty thread still loaded",
    });

    store.getState().showWorkspaceHome();

    expect(store.getState().activeSid).toBeNull();
    await vi.waitFor(() => {
      expect(store.getState().sessions.s1).toMatchObject({
        connected: true,
        transportError: "empty thread still loaded",
      });
    });
  });

  it("startSession also drops a never-connected temp active", async () => {
    const store = createAgentStore();
    mockCreate("s1");
    await store.getState().startSession({ workdir: "/a" });
    mockCreate("s2");
    await store.getState().startSession({ workdir: "/b" });
    expect(store.getState().sessions.s1).toBeUndefined();
    expect(store.getState().sessions.s2).toBeDefined();
    expect(store.getState().activeSid).toBe("s2");
    expect(apiDeleteSession).toHaveBeenCalledWith("s1");
  });

  it("concurrent starts keep the latest request active when responses reorder", async () => {
    const store = createAgentStore();
    let resolveA!: (session: ReturnType<typeof mockCreate>) => void;
    let resolveB!: (session: ReturnType<typeof mockCreate>) => void;
    const first = new Promise<ReturnType<typeof mockCreate>>((resolve) => {
      resolveA = resolve;
    });
    const second = new Promise<ReturnType<typeof mockCreate>>((resolve) => {
      resolveB = resolve;
    });
    apiCreateSession
      .mockImplementationOnce(() => first)
      .mockImplementationOnce(() => second);

    const startA = store.getState().startSession({ workdir: "/a" });
    const startB = store.getState().startSession({ workdir: "/b" });
    resolveB({
      session_id: "s-b",
      runtime: "claude_code",
      native_session_id: null,
      workdir: "/b",
      model: "glm-5.2",
      effort: null,
      permission: "bypassPermissions",
      memory_enabled: true,
      profile_enabled: true,
      capabilities: ["tools"],
      name: "b",
      connected: false,
      running: false,
    });
    await startB;
    resolveA({
      session_id: "s-a",
      runtime: "claude_code",
      native_session_id: null,
      workdir: "/a",
      model: "glm-5.2",
      effort: null,
      permission: "bypassPermissions",
      memory_enabled: true,
      profile_enabled: true,
      capabilities: ["tools"],
      name: "a",
      connected: false,
      running: false,
    });
    await startA;

    expect(store.getState().activeSid).toBe("s-b");
    expect(store.getState().sessions["s-a"]).toBeUndefined();
    expect(apiDeleteSession).toHaveBeenCalledWith("s-a");
  });

  it("Q4: send routes events to the session that opened the stream, not the active one", async () => {
    const store = createAgentStore();
    mockCreate("s1");
    await store.getState().startSession({ workdir: "/a" });
    const first = store.getState().send("one");
    stream.apply!(ev("finished"));
    await releaseAllStreams();
    await first;
    mockCreate("s2");
    await store.getState().startSession({ workdir: "/b" });
    const second = store.getState().send("two");
    stream.apply!(ev("finished"));
    await releaseAllStreams();
    await second;

    await store.getState().activateSession("s1");
    const sending = store.getState().send("hi");
    expect(stream.apply).not.toBeNull();
    stream.apply!(ev("text", { text: "chunk" }));
    await store.getState().activateSession("s2");
    stream.apply!(ev("finished"));
    await releaseAllStreams();
    await sending;

    const firstSession = store.getState().sessions.s1;
    const secondSession = store.getState().sessions.s2;
    expect(firstSession.turns[firstSession.turns.length - 1].items.length).toBeGreaterThan(0);
    expect(secondSession.turns.length).toBe(1);
  });

  it("refuses a second concurrent send into the same session", async () => {
    const store = createAgentStore();
    mockCreate("s1");
    await store.getState().startSession({ workdir: "/wd" });
    const first = store.getState().send("one");
    await store.getState().send("two");
    stream.apply!(ev("finished"));
    await releaseAllStreams();
    await first;
    expect(store.getState().sessions.s1.turns).toHaveLength(1);
  });

  it("session_exited REMOVES the row (no grey/resumable) + clears activeSid", async () => {
    const store = createAgentStore();
    mockCreate("s1");
    await store.getState().startSession({ workdir: "/wd" });
    const sending = store.getState().send("/exit");
    stream.apply!(ev("finished"));
    stream.apply!(ev("session_exited", { returncode: 0 }));
    await releaseAllStreams();
    await sending;
    expect(store.getState().sessions.s1).toBeUndefined();
    expect(store.getState().activeSid).toBeNull();
  });

  it("closeSession removes the row + drops activeSid for the active one", async () => {
    const store = createAgentStore();
    mockCreate("s1");
    await store.getState().startSession({ workdir: "/wd" });
    await store.getState().closeSession("s1");
    expect(store.getState().sessions.s1).toBeUndefined();
    expect(store.getState().activeSid).toBeNull();
    expect(apiDeleteSession).toHaveBeenCalledWith("s1");
  });

  it("marks a slow close as pending and ignores repeated clicks", async () => {
    const store = createAgentStore();
    mockCreate("s1");
    await store.getState().startSession({ workdir: "/wd" });
    let finishClose!: () => void;
    apiDeleteSession.mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          finishClose = () =>
            resolve({
              closed: true,
              status: "closed",
              remaining_resource_count: 0,
              remaining_resource_kinds: [],
              error: null,
            });
        }),
    );

    const first = store.getState().closeSession("s1");
    expect(store.getState().closingSessionIds.has("s1")).toBe(true);
    await store.getState().closeSession("s1");
    expect(apiDeleteSession).toHaveBeenCalledTimes(1);

    finishClose();
    await first;
    expect(store.getState().closingSessionIds.has("s1")).toBe(false);
    expect(store.getState().sessions.s1).toBeUndefined();
  });

  it("keeps the session when close needs reconciliation", async () => {
    const store = createAgentStore();
    mockCreate("s1");
    await store.getState().startSession({ workdir: "/wd" });
    apiDeleteSession.mockResolvedValueOnce({
      closed: false,
      status: "needs_reconcile",
      remaining_resource_count: 1,
      remaining_resource_kinds: ["codex_session_close"],
      error: "Codex close needs reconciliation",
    });

    await store.getState().closeSession("s1");

    expect(store.getState().sessions.s1).toBeDefined();
    expect(store.getState().activeSid).toBe("s1");
    expect(store.getState().sessions.s1.transportError).toMatch(/reconciliation/);
  });

  it("keeps the session when the close request fails", async () => {
    const store = createAgentStore();
    mockCreate("s1");
    await store.getState().startSession({ workdir: "/wd" });
    apiDeleteSession.mockRejectedValueOnce(new Error("sidecar unavailable"));

    await store.getState().closeSession("s1");

    expect(store.getState().sessions.s1).toBeDefined();
    expect(store.getState().activeSid).toBe("s1");
    expect(store.getState().sessions.s1.transportError).toBe("sidecar unavailable");
  });

  it("reset clears all sessions + activeSid", async () => {
    const store = createAgentStore();
    mockCreate("s1");
    await store.getState().startSession({ workdir: "/wd" });
    store.getState().reset();
    expect(store.getState().sessions).toEqual({});
    expect(store.getState().activeSid).toBeNull();
  });

  it("Codex host_status(host_exited) keeps the row", async () => {
    const store = createAgentStore();
    mockCreate("c1", { runtime: "codex" });
    await store.getState().startSession({ workdir: "/wd", runtime: "codex" });
    const sending = store.getState().send("hi");
    stream.apply!(ev("host_status", { status: "host_exited" }, { runtime: "codex" }));
    await releaseAllStreams();
    await sending;
    expect(store.getState().sessions.c1).toBeDefined();
    expect(store.getState().activeSid).toBe("c1");
    expect(store.getState().sessions.c1?.phase).toBe("error");
  });

  it("tasks are per-session (switching does not leak task lists)", async () => {
    const store = createAgentStore();
    mockCreate("s1");
    await store.getState().startSession({ workdir: "/wd" });
    const sending = store.getState().send("do it");
    stream.apply!(
      ev("tool_call", {
        tool_use_id: "tu_1",
        tool_name: "TaskCreate",
        input: { subject: "s1 task" },
      }),
    );
    stream.apply!(ev("finished"));
    await releaseAllStreams();
    await sending;
    expect(store.getState().sessions.s1.tasks).toHaveLength(1);

    mockCreate("s2");
    await store.getState().startSession({ workdir: "/wd" });
    expect(store.getState().sessions.s2.tasks).toHaveLength(0);
    expect(store.getState().sessions.s1.tasks).toHaveLength(1);
  });
});
