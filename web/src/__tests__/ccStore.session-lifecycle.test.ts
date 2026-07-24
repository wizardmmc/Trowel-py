import { describe, expect, it } from "vitest";
import {
  apiCreateSession,
  apiDeleteSession,
  ev,
  mockCreate,
  releaseAllStreams,
  stream,
} from "./ccStoreTestHarness";
import { createCcStore } from "../stores/ccStore";

describe("createCcStore — multi-session lifecycle", () => {
  it("startSession creates a session that is NOT yet connected (not in the bar)", async () => {
    const store = createCcStore();
    mockCreate("s1");
    await store.getState().startSession({ workdir: "/wd" });
    const state = store.getState();
    expect(state.activeSid).toBe("s1");
    expect(state.sessions.s1).toBeDefined();
    expect(state.sessions.s1.connected).toBe(false);
  });

  it("send() flips the session to connected (enters the bar)", async () => {
    const store = createCcStore();
    mockCreate("s1");
    await store.getState().startSession({ workdir: "/wd" });
    expect(store.getState().sessions.s1.connected).toBe(false);
    const sending = store.getState().send("hi");
    expect(store.getState().sessions.s1.connected).toBe(true);
    stream.apply!(ev("finished"));
    await releaseAllStreams();
    await sending;
  });

  it("two CONNECTED sessions coexist; switching preserves both", async () => {
    const store = createCcStore();
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
    const store = createCcStore();
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

  it("startSession also drops a never-connected temp active", async () => {
    const store = createCcStore();
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
    const store = createCcStore();
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
    const store = createCcStore();
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
    const store = createCcStore();
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
    const store = createCcStore();
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
    const store = createCcStore();
    mockCreate("s1");
    await store.getState().startSession({ workdir: "/wd" });
    await store.getState().closeSession("s1");
    expect(store.getState().sessions.s1).toBeUndefined();
    expect(store.getState().activeSid).toBeNull();
    expect(apiDeleteSession).toHaveBeenCalledWith("s1");
  });

  it("reset clears all sessions + activeSid", async () => {
    const store = createCcStore();
    mockCreate("s1");
    await store.getState().startSession({ workdir: "/wd" });
    store.getState().reset();
    expect(store.getState().sessions).toEqual({});
    expect(store.getState().activeSid).toBeNull();
  });

  it("Codex host_status(host_exited) keeps the row", async () => {
    const store = createCcStore();
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
    const store = createCcStore();
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
