import { describe, expect, it, vi } from "vitest";
import {
  apiActivateAgentSession,
  apiCreateSession,
  apiGetEventStream,
  apiGetAgentHistory,
  apiUpdateSessionSettings,
  ev,
  listActiveSessions,
  mockCreate,
  releaseAllStreams,
  stream,
} from "./ccStoreTestHarness";
import { createAgentStore, type AgentSession } from "../agent";
import { getExpectedRuntimePresentation } from "../agent/runtimes";

const CC_CAPABILITIES =
  getExpectedRuntimePresentation("claude_code").expectedCapabilities;
const CODEX_CAPABILITIES =
  getExpectedRuntimePresentation("codex").expectedCapabilities;

/** 创建后端会话目录返回的已连接 CC 记录。 */
function liveSession(sessionId: string, workdir = "/wd"): AgentSession {
  return {
    session_id: sessionId,
    runtime: "claude_code",
    native_session_id: null,
    workdir,
    model: "glm-5.2",
    effort: null,
    permission: null,
    memory_enabled: true,
    profile_enabled: true,
    capabilities: CC_CAPABILITIES,
    name: sessionId,
    connected: true,
    running: false,
  };
}

/** 创建由测试控制完成时机的 Promise。 */
function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => {
    resolve = done;
  });
  return { promise, resolve };
}

describe("createAgentStore — backend session reconciliation", () => {
  it("waits for application watcher readiness before reading the initial snapshot", async () => {
    const store = createAgentStore();
    const firstSnapshot = deferred<
      Awaited<ReturnType<typeof listActiveSessions>>
    >();
    const secondSnapshot = deferred<
      Awaited<ReturnType<typeof listActiveSessions>>
    >();
    let openStream!: () => void;
    apiGetEventStream.mockImplementationOnce(
      (_url, _apply, options) =>
        new Promise<void>((resolve) => {
          openStream = () => options?.onOpen?.("generation-1");
          stream.eventResolvers.push(resolve);
        }),
    );
    listActiveSessions
      .mockReturnValueOnce(firstSnapshot.promise)
      .mockReturnValueOnce(secondSnapshot.promise);

    const refresh = store.getState().refreshActiveSessions();
    await Promise.resolve();
    const refreshRequestedDuringReadiness =
      store.getState().refreshActiveSessions();

    expect(apiGetEventStream).toHaveBeenCalledOnce();
    expect(listActiveSessions).not.toHaveBeenCalled();
    openStream();
    await vi.waitFor(() => expect(listActiveSessions).toHaveBeenCalledOnce());
    firstSnapshot.resolve({ sessions: [], activeId: null });
    await vi.waitFor(() => expect(listActiveSessions).toHaveBeenCalledTimes(2));
    secondSnapshot.resolve({ sessions: [], activeId: null });
    await Promise.all([refresh, refreshRequestedDuringReadiness]);
    expect(listActiveSessions).toHaveBeenCalledTimes(2);
  });

  it("starts the application watcher after an empty initial snapshot", async () => {
    const store = createAgentStore();
    listActiveSessions.mockResolvedValueOnce({ sessions: [], activeId: null });

    await store.getState().refreshActiveSessions();

    expect(apiGetEventStream).toHaveBeenCalledOnce();
  });

  it("keeps the snapshot visible when application watcher readiness times out", async () => {
    const store = createAgentStore({ liveReadinessTimeoutMs: 20 });
    apiGetEventStream.mockRejectedValueOnce(new Error("watcher unavailable"));
    listActiveSessions.mockResolvedValueOnce({
      sessions: [liveSession("s1")],
      activeId: null,
    });

    await store.getState().refreshActiveSessions();

    expect(store.getState().sessions.s1.connected).toBe(true);
    expect(store.getState().sessions.s1.liveState).toBe("reconnecting");
    expect(listActiveSessions).toHaveBeenCalledOnce();
    expect(apiGetEventStream).toHaveBeenCalledOnce();
    store.getState().reset();
  });

  it("pulls backend live sessions without adopting another client's active id", async () => {
    const store = createAgentStore();
    listActiveSessions.mockResolvedValueOnce({
      sessions: [
        {
          session_id: "s1",
          runtime: "claude_code",
          native_session_id: null,
          workdir: "/wd",
          model: "glm-5.2",
          effort: null,
          permission: null,
          memory_enabled: true,
          profile_enabled: true,
          capabilities: CC_CAPABILITIES,
          checkpoint_available: false,
          name: "trowel-py",
          connected: true,
          running: false,
        },
        {
          session_id: "s2",
          runtime: "claude_code",
          native_session_id: null,
          workdir: "/wd",
          model: "glm-5.2",
          effort: null,
          permission: null,
          memory_enabled: true,
          profile_enabled: true,
          capabilities: CC_CAPABILITIES,
          name: "wiki",
          connected: true,
          running: true,
        },
      ],
      activeId: "s1",
    });
    await store.getState().refreshActiveSessions();
    expect(store.getState().sessions.s1).toBeDefined();
    expect(store.getState().sessions.s1.connected).toBe(true);
    expect(store.getState().sessions.s1.checkpointAvailable).toBe(false);
    expect(store.getState().sessions.s2.connected).toBe(true);
    expect(store.getState().activeSid).toBeNull();
  });

  it("keeps session selection local to each renderer store", async () => {
    const sessions = [
      {
        session_id: "s1",
        runtime: "claude_code" as const,
        native_session_id: null,
        workdir: "/one",
        model: "glm-5.2",
        effort: null,
        permission: null,
        memory_enabled: true,
        profile_enabled: true,
        capabilities: CC_CAPABILITIES,
        name: "one",
        connected: true,
        running: false,
      },
      {
        session_id: "s2",
        runtime: "claude_code" as const,
        native_session_id: null,
        workdir: "/two",
        model: "glm-5.2",
        effort: null,
        permission: null,
        memory_enabled: true,
        profile_enabled: true,
        capabilities: CC_CAPABILITIES,
        name: "two",
        connected: true,
        running: false,
      },
    ];
    listActiveSessions
      .mockResolvedValueOnce({ sessions, activeId: "s1" })
      .mockResolvedValueOnce({ sessions, activeId: "s1" });
    const webStore = createAgentStore();
    const desktopStore = createAgentStore();
    await webStore.getState().refreshActiveSessions();
    await desktopStore.getState().refreshActiveSessions();

    await webStore.getState().activateSession("s1");
    await desktopStore.getState().activateSession("s2");

    expect(webStore.getState().activeSid).toBe("s1");
    expect(desktopStore.getState().activeSid).toBe("s2");
    expect(apiActivateAgentSession).not.toHaveBeenCalled();
  });

  it("reconciles tracked sessions from the backend lifecycle snapshot", async () => {
    const store = createAgentStore();
    mockCreate("s1");
    await store.getState().startSession({ workdir: "/wd" });
    listActiveSessions.mockResolvedValueOnce({
      sessions: [
        {
          session_id: "s1",
          runtime: "claude_code",
          native_session_id: null,
          workdir: "/wd",
          model: "glm-5.2",
          effort: null,
          permission: null,
          memory_enabled: true,
          profile_enabled: true,
          capabilities: CC_CAPABILITIES,
          name: "renamed",
          display_title: "renamed",
          title_source: "manual",
          connected: true,
          running: false,
          resource_state: "connected",
          turn_state: "completed",
          state_generation: 2,
          last_event_seq: 7,
        },
      ],
      activeId: "s1",
    });
    await store.getState().refreshActiveSessions();
    await vi.waitFor(() =>
      expect(store.getState().sessions.s1).toMatchObject({
        connected: true,
        displayTitle: "renamed",
        resourceState: "connected",
        turnState: "completed",
        stateGeneration: 2,
        lastSeq: 7,
        liveState: "ready",
      }),
    );
  });

  it("silently no-ops when the backend is unreachable", async () => {
    const store = createAgentStore();
    listActiveSessions.mockRejectedValueOnce(new Error("backend down"));
    await store.getState().refreshActiveSessions();
    expect(store.getState().sessions).toEqual({});
  });

  it("removes a user session closed by another renderer", async () => {
    const store = createAgentStore();
    listActiveSessions
      .mockResolvedValueOnce({ sessions: [liveSession("s1")], activeId: null })
      .mockResolvedValueOnce({ sessions: [], activeId: null });
    await store.getState().refreshActiveSessions();
    await store.getState().activateSession("s1");

    await store.getState().refreshActiveSessions();

    expect(store.getState().sessions.s1).toBeUndefined();
    expect(store.getState().activeSid).toBeNull();
  });

  it("ignores an older session catalog response that arrives last", async () => {
    const store = createAgentStore();
    const older = deferred<{
      readonly sessions: readonly AgentSession[];
      readonly activeId: string | null;
    }>();
    listActiveSessions
      .mockReturnValueOnce(older.promise)
      .mockResolvedValueOnce({
        sessions: [liveSession("newer", "/newer")],
        activeId: null,
      });

    const firstRefresh = store.getState().refreshActiveSessions();
    const secondRefresh = store.getState().refreshActiveSessions();
    older.resolve({
      sessions: [liveSession("older", "/older")],
      activeId: null,
    });
    await Promise.all([firstRefresh, secondRefresh]);

    expect(Object.keys(store.getState().sessions)).toEqual(["newer"]);
  });

  it("ignores non-user rows if a backend returns them", async () => {
    const store = createAgentStore();
    listActiveSessions.mockResolvedValueOnce({
      sessions: [
        {
          session_id: "delegate",
          runtime: "codex",
          native_session_id: "thread-delegate",
          workdir: "/wd",
          model: "gpt-5.6-sol",
          effort: "high",
          permission: null,
          memory_enabled: true,
          profile_enabled: true,
          capabilities: ["tools", "approval", "subagents"],
          name: "internal",
          connected: true,
          running: true,
          session_kind: "delegate",
        },
        {
          session_id: "probe",
          runtime: "codex",
          native_session_id: "thread-probe",
          workdir: "/wd",
          model: "gpt-5.6-sol",
          effort: "high",
          permission: null,
          memory_enabled: false,
          profile_enabled: false,
          capabilities: ["tools", "approval", "subagents"],
          name: "probe",
          connected: true,
          running: true,
          session_kind: "probe",
        },
      ],
      activeId: "delegate",
    });

    await store.getState().refreshActiveSessions();

    expect(store.getState().sessions).toEqual({});
    expect(store.getState().activeSid).toBeNull();
  });

  it("limits startup history reconciliation to three concurrent requests", async () => {
    const store = createAgentStore();
    const sessions = Array.from({ length: 20 }, (_, index) => ({
      ...liveSession(`s${index}`),
      last_event_seq: 1,
      state_generation: 1,
    }));
    let inFlight = 0;
    let maxInFlight = 0;
    apiGetAgentHistory.mockImplementation(async () => {
      inFlight += 1;
      maxInFlight = Math.max(maxInFlight, inFlight);
      await new Promise((resolve) => window.setTimeout(resolve, 5));
      inFlight -= 1;
      return [];
    });
    listActiveSessions.mockResolvedValueOnce({ sessions, activeId: null });

    await store.getState().refreshActiveSessions();

    expect(apiGetAgentHistory).toHaveBeenCalledTimes(20);
    expect(maxInFlight).toBeLessThanOrEqual(3);
  });

  it("coalesces periodic snapshots while history reconciliation is stalled", async () => {
    const store = createAgentStore();
    const sessions = Array.from({ length: 20 }, (_, index) => ({
      ...liveSession(`s${index}`),
      last_event_seq: 1,
      state_generation: 1,
    }));
    const firstHistoryBatch = deferred<readonly []>();
    const secondHistoryBatch = deferred<readonly []>();
    apiGetAgentHistory.mockImplementation(() =>
      listActiveSessions.mock.calls.length === 1
        ? firstHistoryBatch.promise
        : secondHistoryBatch.promise,
    );
    listActiveSessions
      .mockResolvedValueOnce({ sessions, activeId: null })
      .mockResolvedValueOnce({
        sessions: sessions.map((session) => ({
          ...session,
          last_event_seq: 2,
          state_generation: 2,
        })),
        activeId: null,
      });

    const initial = store.getState().refreshActiveSessions();
    await vi.waitFor(() => expect(apiGetAgentHistory).toHaveBeenCalledTimes(3));
    const periodic = store.getState().refreshActiveSessions();
    const gapRecovery = store.getState().refreshActiveSessions();
    await Promise.resolve();

    expect(listActiveSessions).toHaveBeenCalledOnce();
    expect(apiGetAgentHistory).toHaveBeenCalledTimes(3);

    firstHistoryBatch.resolve([]);
    await vi.waitFor(() => expect(listActiveSessions).toHaveBeenCalledTimes(2));
    await vi.waitFor(() => expect(apiGetAgentHistory).toHaveBeenCalledTimes(23));
    const periodicDuringFollowUp = store.getState().refreshActiveSessions();
    const gapDuringFollowUp = store.getState().refreshActiveSessions();

    secondHistoryBatch.resolve([]);
    await Promise.all([
      initial,
      periodic,
      gapRecovery,
      periodicDuringFollowUp,
      gapDuringFollowUp,
    ]);
    expect(listActiveSessions).toHaveBeenCalledTimes(2);
    expect(apiGetAgentHistory).toHaveBeenCalledTimes(40);
  });

  it("materializes an unknown session without replaying history over buffered live events", async () => {
    const store = createAgentStore();
    const first = mockCreate("s1", {
      runtime: "codex",
      native_session_id: "thread-1",
      capabilities: CODEX_CAPABILITIES,
    });
    await store.getState().startSession({ workdir: "/wd", runtime: "codex" });
    const snapshot = deferred<Awaited<ReturnType<typeof listActiveSessions>>>();
    listActiveSessions.mockReturnValueOnce(snapshot.promise);
    stream.apply!(
      ev(
        "turn_start",
        { autonomous: true },
        {
          session_id: "s2",
          runtime: "codex",
          thread_id: "thread-2",
          turn_id: "turn-2",
          seq: 1,
        },
      ),
    );
    stream.apply!(
      ev(
        "text",
        { text: "only once" },
        {
          session_id: "s2",
          runtime: "codex",
          thread_id: "thread-2",
          turn_id: "turn-2",
          seq: 2,
        },
      ),
    );
    snapshot.resolve({
      sessions: [
        { ...first, connected: true },
        {
          ...first,
          session_id: "s2",
          native_session_id: "thread-2",
          name: "s2",
          connected: true,
          running: true,
          turn_state: "running",
          current_turn_id: "turn-2",
          last_event_seq: 2,
        },
      ],
      activeId: "s1",
    });

    await vi.waitFor(() =>
      expect(store.getState().sessions.s2).toMatchObject({
        currentTurnId: "turn-2",
        turnState: "running",
        lastSeq: 2,
      }),
    );
    expect(store.getState().sessions.s2.turns[0].items).toEqual([
      { kind: "text", text: "only once" },
    ]);
    expect(apiGetAgentHistory).not.toHaveBeenCalled();
  });

  it("drops orphaned events before a later session reuses the same id", async () => {
    const store = createAgentStore();
    listActiveSessions
      .mockResolvedValueOnce({ sessions: [], activeId: null })
      .mockResolvedValueOnce({ sessions: [], activeId: null });
    await store.getState().refreshActiveSessions();

    stream.eventApply!(
      ev(
        "text",
        { text: "orphaned" },
        { session_id: "reused", turn_id: "turn-new", seq: 1 },
      ),
    );
    await vi.waitFor(() => expect(listActiveSessions).toHaveBeenCalledTimes(2));
    await new Promise((resolve) => window.setTimeout(resolve, 0));

    listActiveSessions.mockResolvedValueOnce({
      sessions: [
        {
          ...liveSession("reused"),
          running: true,
          turn_state: "running",
          current_turn_id: "turn-new",
          last_event_seq: 2,
        },
      ],
      activeId: null,
    });
    stream.eventApply!(
      ev(
        "turn_start",
        { autonomous: true },
        { session_id: "reused", turn_id: "turn-new", seq: 1 },
      ),
    );
    stream.eventApply!(
      ev(
        "text",
        { text: "current" },
        { session_id: "reused", turn_id: "turn-new", seq: 2 },
      ),
    );

    await vi.waitFor(() =>
      expect(store.getState().sessions.reused?.lastSeq).toBe(2),
    );
    expect(store.getState().sessions.reused.turns[0].items).toEqual([
      { kind: "text", text: "current" },
    ]);
  });

  it("flushes buffered events after periodic recovery materializes the session", async () => {
    const store = createAgentStore();
    listActiveSessions
      .mockResolvedValueOnce({ sessions: [], activeId: null })
      .mockRejectedValueOnce(new Error("snapshot unavailable"));
    await store.getState().refreshActiveSessions();
    stream.eventApply!(
      ev(
        "turn_start",
        { autonomous: true },
        { session_id: "recovered", turn_id: "turn-1", seq: 1 },
      ),
    );
    await vi.waitFor(() => expect(listActiveSessions).toHaveBeenCalledTimes(2));
    await new Promise((resolve) => window.setTimeout(resolve, 0));

    listActiveSessions.mockResolvedValueOnce({
      sessions: [
        {
          ...liveSession("recovered"),
          running: true,
          turn_state: "running",
          current_turn_id: "turn-1",
          last_event_seq: null,
        },
      ],
      activeId: null,
    });
    await store.getState().refreshActiveSessions();
    stream.eventApply!(
      ev(
        "text",
        { text: "after recovery" },
        { session_id: "recovered", turn_id: "turn-1", seq: 2 },
      ),
    );

    expect(store.getState().sessions.recovered).toMatchObject({
      currentTurnId: "turn-1",
      lastSeq: 2,
      turnState: "running",
    });
    expect(store.getState().sessions.recovered.turns[0].items).toEqual([
      { kind: "text", text: "after recovery" },
    ]);
  });

  it("creates a turn container when a handoff snapshot arrives before its first visible event", async () => {
    const store = createAgentStore();
    listActiveSessions.mockResolvedValueOnce({
      sessions: [
        {
          ...liveSession("handoff"),
          runtime: "codex",
          native_session_id: "thread-handoff",
          model: "gpt-5.6-sol",
          effort: "high",
          capabilities: CODEX_CAPABILITIES,
          running: true,
          turn_state: "running",
          current_turn_id: "turn-handoff",
          last_event_seq: null,
        },
      ],
      activeId: null,
    });

    await store.getState().refreshActiveSessions();
    stream.eventApply!(
      ev(
        "tool_call",
        { tool_use_id: "tool-1", tool_name: "Read", input: {} },
        {
          session_id: "handoff",
          runtime: "codex",
          thread_id: "thread-handoff",
          turn_id: "turn-handoff",
          item_id: "tool-1",
          seq: 1,
        },
      ),
    );

    const handoff = store.getState().sessions.handoff;
    expect(handoff.turns).toHaveLength(1);
    expect(handoff.turns[0].items).toEqual([
      expect.objectContaining({ kind: "tool", toolName: "Read" }),
    ]);
    expect([handoff.meta.model, handoff.effort]).toEqual([
      "gpt-5.6-sol",
      "high",
    ]);
  });

  it.each([
    {
      eventCount: 128,
      expectedLiveState: "ready",
      expectedTurnState: "running",
    },
    {
      eventCount: 129,
      expectedLiveState: "gapped",
      expectedTurnState: "unknown",
    },
  ] as const)(
    "distinguishes a full unknown-session buffer from a real overflow ($eventCount events)",
    async ({ eventCount, expectedLiveState, expectedTurnState }) => {
      const store = createAgentStore();
      const first = mockCreate("s1", {
        runtime: "codex",
        native_session_id: "thread-1",
        capabilities: CODEX_CAPABILITIES,
      });
      await store.getState().startSession({ workdir: "/wd", runtime: "codex" });
      const snapshot =
        deferred<Awaited<ReturnType<typeof listActiveSessions>>>();
      listActiveSessions.mockReturnValueOnce(snapshot.promise);

      for (let seq = 1; seq <= eventCount; seq += 1) {
        stream.apply!(
          ev(
            "text",
            { text: `part-${seq}` },
            {
              session_id: "s2",
              runtime: "codex",
              thread_id: "thread-2",
              turn_id: "turn-2",
              seq,
            },
          ),
        );
      }
      snapshot.resolve({
        sessions: [
          { ...first, connected: true },
          {
            ...first,
            session_id: "s2",
            native_session_id: "thread-2",
            name: "s2",
            connected: true,
            running: true,
            turn_state: "running",
            current_turn_id: "turn-2",
            last_event_seq: eventCount,
          },
        ],
        activeId: "s1",
      });

      await vi.waitFor(() =>
        expect(store.getState().sessions.s2).toMatchObject({
          liveState: expectedLiveState,
          turnState: expectedTurnState,
          lastSeq: eventCount,
        }),
      );
    },
  );

  it("keeps a buffered session gapped when its middle sequence is missing", async () => {
    const store = createAgentStore();
    const first = mockCreate("s1", {
      runtime: "codex",
      native_session_id: "thread-1",
      capabilities: CODEX_CAPABILITIES,
    });
    await store.getState().startSession({ workdir: "/wd", runtime: "codex" });
    const snapshot = deferred<Awaited<ReturnType<typeof listActiveSessions>>>();
    listActiveSessions.mockReturnValueOnce(snapshot.promise);
    stream.apply!(
      ev(
        "text",
        { text: "first" },
        {
          session_id: "s2",
          runtime: "codex",
          thread_id: "thread-2",
          turn_id: "turn-2",
          seq: 1,
        },
      ),
    );
    stream.apply!(
      ev(
        "text",
        { text: "third" },
        {
          session_id: "s2",
          runtime: "codex",
          thread_id: "thread-2",
          turn_id: "turn-2",
          seq: 3,
        },
      ),
    );
    snapshot.resolve({
      sessions: [
        { ...first, connected: true },
        {
          ...first,
          session_id: "s2",
          native_session_id: "thread-2",
          connected: true,
          running: true,
          turn_state: "running",
          current_turn_id: "turn-2",
          last_event_seq: 3,
        },
      ],
      activeId: "s1",
    });

    await vi.waitFor(() =>
      expect(store.getState().sessions.s2).toMatchObject({
        lastSeq: 3,
        liveState: "gapped",
        turnState: "unknown",
        needsReplay: true,
      }),
    );
  });

  it("does not let a null-watermark snapshot overwrite a first live event", async () => {
    const store = createAgentStore();
    const created = mockCreate("s1", { connected: true });
    await store.getState().startSession({ workdir: "/wd" });
    await store.getState().send("hello");
    const history = deferred<readonly []>();
    listActiveSessions
      .mockResolvedValueOnce({
        sessions: [
          {
            ...created,
            connected: true,
            running: true,
            turn_state: "running",
            current_turn_id: "turn-1",
            state_generation: 2,
            last_event_seq: null,
          },
        ],
        activeId: "s1",
      })
      .mockRejectedValueOnce(new Error("retry snapshot unavailable"));
    apiGetAgentHistory.mockReturnValueOnce(history.promise);

    stream.eventControl!({ type: "gap", sessionId: "s1" });
    await vi.waitFor(() => expect(apiGetAgentHistory).toHaveBeenCalledOnce());
    stream.apply!(
      ev(
        "text",
        { text: "first live event" },
        {
          turn_id: "turn-1",
          seq: 1,
        },
      ),
    );
    history.resolve([]);
    await vi.waitFor(() => expect(listActiveSessions).toHaveBeenCalledTimes(2));

    expect(store.getState().sessions.s1).toMatchObject({
      lastSeq: 1,
      needsReplay: true,
    });
  });
});

describe("createAgentStore — memory/profile A/B switches", () => {
  it("startSession stores the condition from the backend response", async () => {
    const store = createAgentStore();
    mockCreate("s1", { memory_enabled: false, profile_enabled: true });
    await store.getState().startSession({
      workdir: "/wd",
      memory_enabled: false,
      profile_enabled: true,
    });
    expect(store.getState().sessions.s1.memoryEnabled).toBe(false);
    expect(store.getState().sessions.s1.profileEnabled).toBe(true);
  });

  it("startSession forwards the switches to the API", async () => {
    const store = createAgentStore();
    mockCreate("s1");
    await store.getState().startSession({
      workdir: "/wd",
      memory_enabled: false,
      profile_enabled: false,
    });
    expect(apiCreateSession).toHaveBeenCalledWith(
      expect.objectContaining({
        memory_enabled: false,
        profile_enabled: false,
      }),
      expect.any(String),
    );
  });

  it("refreshActiveSessions reconciles the condition from the backend", async () => {
    const store = createAgentStore();
    listActiveSessions.mockResolvedValueOnce({
      sessions: [
        {
          session_id: "s1",
          runtime: "claude_code",
          native_session_id: null,
          workdir: "/wd",
          model: "m",
          effort: null,
          permission: null,
          memory_enabled: false,
          profile_enabled: true,
          capabilities: CC_CAPABILITIES,
          name: "wd",
          connected: true,
          running: false,
        },
      ],
      activeId: "s1",
    });
    await store.getState().refreshActiveSessions();
    expect(store.getState().sessions.s1.memoryEnabled).toBe(false);
    expect(store.getState().sessions.s1.profileEnabled).toBe(true);
  });
});

describe("createAgentStore — Codex next-turn settings", () => {
  it("stores the backend-validated model/effort pair as pending", async () => {
    const store = createAgentStore();
    mockCreate("s1", {
      runtime: "codex",
      model: "gpt-5.6-sol",
      effort: "low",
      capabilities: CODEX_CAPABILITIES,
    });
    apiUpdateSessionSettings.mockResolvedValueOnce({
      model: "gpt-5.6-luna",
      effort: "medium",
      adjusted: true,
    });
    await store.getState().startSession({ workdir: "/wd", runtime: "codex" });
    await store.getState().updateSessionSettings("gpt-5.6-luna", "ultra");
    const session = store.getState().sessions.s1;
    expect(apiUpdateSessionSettings).toHaveBeenCalledWith("s1", {
      model: "gpt-5.6-luna",
      effort: "ultra",
    });
    expect([session.pendingModel, session.pendingEffort]).toEqual([
      "gpt-5.6-luna",
      "medium",
    ]);
    expect(session.settingsNotice).toContain("已改为 medium");
  });

  it("commits and clears the pending pair only on model_changed", async () => {
    const store = createAgentStore();
    mockCreate("s1", {
      runtime: "codex",
      model: "gpt-5.6-sol",
      effort: "low",
      capabilities: CODEX_CAPABILITIES,
    });
    apiUpdateSessionSettings.mockResolvedValueOnce({
      model: "gpt-5.6-luna",
      effort: "medium",
      adjusted: false,
    });
    await store.getState().startSession({ workdir: "/wd", runtime: "codex" });
    await store.getState().updateSessionSettings("gpt-5.6-luna", "medium");
    const sending = store.getState().send("hi");
    stream.apply!(
      ev(
        "model_changed",
        { model: "gpt-5.6-luna", effort: "medium" },
        { runtime: "codex" },
      ),
    );
    expect(store.getState().sessions.s1).toMatchObject({
      effort: "medium",
      pendingModel: null,
      pendingEffort: null,
      settingsNotice: null,
    });
    await releaseAllStreams();
    await sending;
  });

  it("applies native effective permission facts from lazy thread/start", async () => {
    const store = createAgentStore();
    mockCreate("s1", {
      runtime: "codex",
      permission_preset: "follow",
      capabilities: CODEX_CAPABILITIES,
    });
    await store.getState().startSession({ workdir: "/wd", runtime: "codex" });
    const sending = store.getState().send("hi");
    stream.apply!(
      ev(
        "session_started",
        {
          model: "gpt-5.6-sol",
          cwd: "/wd",
          cc_session_id: "thread-1",
          tools: [],
          permission_profile: ":read-only",
          effective_sandbox: "read-only",
          effective_approval: "on-request",
          network_access: false,
        },
        { runtime: "codex" },
      ),
    );
    expect(store.getState().sessions.s1).toMatchObject({
      nativeSessionId: "thread-1",
      permission: "Read only · on-request",
      permissionPreset: "follow",
      effectivePermissionProfile: ":read-only",
      effectiveSandbox: "read-only",
      effectiveApproval: "on-request",
      networkAccess: false,
    });
    await releaseAllStreams();
    await sending;
  });
});
