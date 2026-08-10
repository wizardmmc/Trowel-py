import { describe, expect, it, vi } from "vitest";
import {
  apiAnswerAgentRequest,
  apiCreateSession,
  apiGetAgentHistory,
  apiListAgentRequests,
  apiStartAgentTurn,
  ev,
  mockCreate,
  listActiveSessions,
  releaseAllStreams,
  stream,
} from "./ccStoreTestHarness";
import { createAgentStore } from "../agent";
import {
  createReconciledSessionState,
  materializeCurrentRootTurn,
} from "../agent/application/store/sessionState";

describe("createAgentStore — approval recovery", () => {
  it("keeps awaiting_input when an empty history needs a root turn container", () => {
    const snapshot = mockCreate("s1", {
      connected: true,
      running: true,
      turn_state: "awaiting_input",
      current_turn_id: "turn-1",
    });

    const materialized = materializeCurrentRootTurn(
      createReconciledSessionState(snapshot),
    );

    expect(materialized.turns).toMatchObject([
      { turnId: "turn-1", status: "active" },
    ]);
    expect(materialized.phase).toBe("awaiting_input");
  });

  it("rebuilds the active turn before restoring a pending approval after reload", async () => {
    const store = createAgentStore();
    const session = mockCreate("s1", {
      runtime: "codex",
      native_session_id: "thread-1",
      model: "gpt-5.6-sol",
      capabilities: ["tools", "approval"],
      connected: true,
      running: true,
      turn_state: "awaiting_input",
      current_turn_id: "turn-1",
      state_generation: 2,
      last_event_seq: 2,
    });
    apiCreateSession.mockReset();
    listActiveSessions.mockResolvedValueOnce({ sessions: [session], activeId: "s1" });
    apiGetAgentHistory.mockResolvedValueOnce([
      ev("turn_start", {}, { session_id: "s1", turn_id: "turn-1", seq: 1 }),
      ev(
        "approval_request",
        {
          request_id: "7-0",
          item_id: "exec-1",
          approval_kind: "command_approval",
          command: "pwd",
          cwd: "/wd",
          reason: "Allow it?",
          available_decisions: ["accept", "cancel"],
          status: "pending",
          decision: null,
          auto_resolved: false,
          resolution_reason: null,
        },
        { session_id: "s1", runtime: "codex", turn_id: "turn-1", seq: 2 },
      ),
    ]);
    apiListAgentRequests.mockResolvedValueOnce([
      {
        request_id: "7-0",
        session_id: "s1",
        thread_id: "thread-1",
        turn_id: "turn-1",
        item_id: "exec-1",
        approval_kind: "command_approval",
        command: "pwd",
        cwd: "/wd",
        reason: "Allow it?",
        available_decisions: ["accept", "cancel"],
        status: "pending",
        decision: null,
        auto_resolved: false,
        resolution_reason: null,
      },
    ]);

    await store.getState().refreshActiveSessions();
    await store.getState().activateSession("s1");

    expect(store.getState().sessions.s1.turns).toHaveLength(1);
    expect(store.getState().sessions.s1.turns[0]).toMatchObject({
      turnId: "turn-1",
      status: "active",
      items: [{ kind: "approval", requestId: "7-0", status: "pending" }],
    });
    expect(store.getState().sessions.s1).toMatchObject({
      phase: "awaiting_input",
      turnState: "awaiting_input",
    });

    const historyCalls = apiGetAgentHistory.mock.calls.length;
    await store.getState().loadHistoryIntoView();
    expect(apiGetAgentHistory).toHaveBeenCalledTimes(historyCalls);
    expect(store.getState().sessions.s1.turns[0].items).toMatchObject([
      { kind: "approval", requestId: "7-0", status: "pending" },
    ]);
  });

  it("restores the approval again when activation races with history replay", async () => {
    const store = createAgentStore();
    const session = mockCreate("s1", {
      runtime: "codex",
      native_session_id: "thread-1",
      capabilities: ["tools", "approval"],
      connected: true,
      running: true,
      turn_state: "awaiting_input",
      current_turn_id: "turn-1",
      state_generation: 2,
      last_event_seq: 2,
    });
    apiCreateSession.mockReset();
    listActiveSessions.mockResolvedValueOnce({ sessions: [session], activeId: "s1" });
    let resolveHistory!: (events: readonly ReturnType<typeof ev>[]) => void;
    apiGetAgentHistory.mockReturnValueOnce(
      new Promise((resolve) => {
        resolveHistory = resolve;
      }),
    );
    const pendingRequest = {
      request_id: "7-0",
      session_id: "s1",
      thread_id: "thread-1",
      turn_id: "turn-1",
      item_id: "exec-1",
      approval_kind: "command_approval" as const,
      command: "pwd",
      cwd: "/wd",
      reason: "Allow it?",
      available_decisions: ["accept", "cancel"],
      status: "pending" as const,
      decision: null,
      auto_resolved: false,
      resolution_reason: null,
    };
    apiListAgentRequests.mockResolvedValue([pendingRequest]);

    const refresh = store.getState().refreshActiveSessions();
    await vi.waitFor(() => expect(store.getState().sessions.s1).toBeDefined());
    const activation = store.getState().activateSession("s1");
    resolveHistory([]);
    await Promise.all([refresh, activation]);

    expect(store.getState().sessions.s1.turns).toMatchObject([
      {
        turnId: "turn-1",
        status: "active",
        items: [{ kind: "approval", requestId: "7-0", status: "pending" }],
      },
    ]);
    expect(store.getState().sessions.s1.phase).toBe("awaiting_input");
    apiListAgentRequests.mockReset();
    apiListAgentRequests.mockResolvedValue([]);
  });

  it("closes an active renderer turn when an equal-seq snapshot is terminal", async () => {
    const store = createAgentStore();
    const session = mockCreate("s1", {
      runtime: "codex",
      native_session_id: "thread-1",
      capabilities: ["tools", "approval"],
      connected: true,
      running: true,
      turn_state: "awaiting_input",
      current_turn_id: "turn-1",
      state_generation: 1,
      last_event_seq: 1,
    });
    apiCreateSession.mockReset();
    listActiveSessions
      .mockResolvedValueOnce({ sessions: [session], activeId: "s1" })
      .mockResolvedValueOnce({
        sessions: [
          {
            ...session,
            running: false,
            turn_state: "completed",
          },
        ],
        activeId: "s1",
      });
    apiGetAgentHistory.mockResolvedValueOnce([
      ev("turn_start", {}, {
        session_id: "s1",
        runtime: "codex",
        thread_id: "thread-1",
        turn_id: "turn-1",
        seq: 1,
      }),
    ]);

    await store.getState().refreshActiveSessions();
    expect(store.getState().sessions.s1.turns).toMatchObject([
      { turnId: "turn-1", status: "active" },
    ]);
    const historyCalls = apiGetAgentHistory.mock.calls.length;
    await store.getState().refreshActiveSessions();

    expect(store.getState().sessions.s1).toMatchObject({
      phase: "done",
      turnState: "completed",
      turns: [{ turnId: "turn-1", status: "done" }],
    });
    expect(apiGetAgentHistory).toHaveBeenCalledTimes(historyCalls);
  });

  it("folds the answer response into the pending card when SSE is unavailable", async () => {
    const store = createAgentStore();
    mockCreate("s1", {
      runtime: "codex",
      model: "gpt-5.6-sol",
      capabilities: ["tools", "approval"],
    });
    await store.getState().startSession({ workdir: "/wd", runtime: "codex" });
    await store.getState().send("run it");
    stream.apply!(
      ev(
        "approval_request",
        {
          request_id: "7-0",
          item_id: "exec-1",
          approval_kind: "command_approval",
          command: "pwd",
          cwd: "/wd",
          reason: "Allow it?",
          available_decisions: ["accept", "cancel"],
          status: "pending",
          decision: null,
          auto_resolved: false,
          resolution_reason: null,
        },
        { runtime: "codex", turn_id: "turn-1" },
      ),
    );
    apiAnswerAgentRequest.mockRejectedValueOnce(new Error("answer failed"));
    await store.getState().answerApproval("7-0", "accept");
    expect(store.getState().sessions.s1.transportProblem?.operation).toBe(
      "approval_answer",
    );
    apiAnswerAgentRequest.mockResolvedValue({
      answered: true,
      request: {
        request_id: "7-0",
        session_id: "s1",
        thread_id: "thread-1",
        turn_id: "turn-1",
        item_id: "exec-1",
        approval_kind: "command_approval",
        command: "pwd",
        cwd: "/wd",
        reason: "Allow it?",
        available_decisions: ["accept", "cancel"],
        status: "answered",
        decision: "accept",
        auto_resolved: false,
        resolution_reason: null,
      },
    });

    await store.getState().answerApproval("7-0", "accept");

    expect(store.getState().sessions.s1.turns[0].items[0]).toMatchObject({
      kind: "approval",
      requestId: "7-0",
      status: "answered",
      decision: "accept",
    });
    expect(store.getState().sessions.s1.transportProblem).toBeNull();
  });
});

describe("createAgentStore — per-session event sequence", () => {
  it("drops a duplicate seq (re-delivered event does not double-append)", async () => {
    const store = createAgentStore();
    mockCreate("s1");
    await store.getState().startSession({ workdir: "/wd" });
    await store.getState().send("hi");
    stream.apply!(ev("text", { text: "a" }));
    stream.apply!(ev("text", { text: "b" }, { seq: 1 }));
    stream.apply!(ev("finished", {}, { turn_id: "turn-1" }));
    const turn = store.getState().sessions.s1.turns[0];
    expect(turn.items.filter((item) => item.kind === "text")).toHaveLength(1);
    expect((turn.items[0] as { text: string }).text).toBe("a");
  });

  it("flags needsReplay when a seq gap is observed", async () => {
    const store = createAgentStore();
    mockCreate("s1");
    await store.getState().startSession({ workdir: "/wd" });
    await store.getState().send("hi");
    let resolveSnapshot!: (
      value: Awaited<ReturnType<typeof listActiveSessions>>,
    ) => void;
    listActiveSessions.mockReturnValueOnce(
      new Promise((resolve) => {
        resolveSnapshot = resolve;
      }),
    );
    stream.apply!(ev("text", { text: "a" }));
    stream.apply!(ev("text", { text: "b" }, { seq: 5 }));
    stream.apply!(ev("finished", {}, { turn_id: "turn-1" }));
    expect(store.getState().sessions.s1.needsReplay).toBe(true);
    await vi.waitFor(() => expect(listActiveSessions).toHaveBeenCalledOnce());
    resolveSnapshot({ sessions: [], activeId: null });
  });

  it("seq is per-session (two streams do not share the counter)", async () => {
    const store = createAgentStore();
    mockCreate("s1");
    await store.getState().startSession({ workdir: "/a" });
    await store.getState().send("one");
    stream.apply!(ev("text", { text: "a" }));
    stream.apply!(ev("finished", {}, { turn_id: "turn-1" }));
    mockCreate("s2");
    await store.getState().startSession({ workdir: "/b" });
    await store.getState().send("two");
    stream.apply!(ev("text", { text: "b" }, { seq: 1, session_id: "s2" }));
    stream.apply!(
      ev("finished", {}, { seq: 2, session_id: "s2", turn_id: "turn-1" }),
    );
    expect(store.getState().sessions.s1.lastSeq).toBe(2);
    expect(store.getState().sessions.s2.lastSeq).toBe(2);
    expect(store.getState().sessions.s2.needsReplay).toBe(false);
  });

  it("does not accept a low seq after restart without a new stream generation", async () => {
    const store = createAgentStore();
    apiStartAgentTurn
      .mockResolvedValueOnce({ turnId: "turn-1" })
      .mockResolvedValueOnce({ turnId: "turn-2" });
    mockCreate("s1", {
      runtime: "codex",
      native_session_id: "thread-1",
      capabilities: ["tools"],
    });
    await store.getState().startSession({ workdir: "/wd", runtime: "codex" });

    await store.getState().send("first");
    stream.apply!(
      ev("finished", {}, {
        runtime: "codex",
        thread_id: "thread-1",
        turn_id: "turn-1",
        seq: 87,
      }),
    );

    await store.getState().send("please commit");
    stream.apply!(
      ev(
        "error",
        {
          subclass: "host_error",
          errors: ["codex session s1 not live"],
          api_error_status: null,
        },
        {
          runtime: "codex",
          thread_id: "thread-1",
          turn_id: "turn-2",
          seq: 1,
        },
      ),
    );

    const session = store.getState().sessions.s1;
    const turn = session.turns.at(-1)!;
    expect(session.turnState).toBe("running");
    expect(turn.status).toBe("active");
    expect(turn.items.filter((item) => item.kind === "error")).toHaveLength(0);
    expect(session.lastSeq).toBe(87);
  });

  it("resets process-local watermarks when the sidecar generation changes", async () => {
    const store = createAgentStore();
    mockCreate("s1", {
      state_generation: 42,
      last_event_seq: 87,
    });
    await store.getState().startSession({ workdir: "/wd" });
    await store.getState().send("hello");

    stream.eventControl!({ type: "ready", generation: "generation-2" });

    expect(store.getState().sessions.s1).toMatchObject({
      stateGeneration: 0,
      lastSeq: null,
      needsReplay: true,
      liveState: "reconnecting",
    });
  });

  it("still flags a seq gap when the current request error moves forward", async () => {
    const store = createAgentStore();
    mockCreate("s1", {
      runtime: "codex",
      native_session_id: "thread-1",
      capabilities: ["tools"],
    });
    await store.getState().startSession({ workdir: "/wd", runtime: "codex" });

    await store.getState().send("hello");
    stream.apply!(
      ev("text", { text: "partial" }, {
        runtime: "codex",
        thread_id: "thread-1",
        turn_id: "turn-1",
        seq: 1,
      }),
    );
    stream.apply!(
      ev(
        "error",
        {
          subclass: "host_error",
          errors: ["stream failed"],
          api_error_status: null,
        },
        {
          runtime: "codex",
          thread_id: "thread-1",
          turn_id: "turn-1",
          seq: 3,
        },
      ),
    );

    const session = store.getState().sessions.s1;
    expect(session.turnState).toBe("unknown");
    expect(session.lastSeq).toBe(3);
    expect(session.needsReplay).toBe(true);
  });

  it("does not let an old turn terminal finish the current turn", async () => {
    const store = createAgentStore();
    apiStartAgentTurn
      .mockResolvedValueOnce({ turnId: "turn-1" })
      .mockResolvedValueOnce({ turnId: "turn-2" });
    mockCreate("s1", {
      runtime: "codex",
      native_session_id: "thread-1",
      capabilities: ["tools"],
    });
    await store.getState().startSession({ workdir: "/wd", runtime: "codex" });

    await store.getState().send("first");
    stream.apply!(
      ev("finished", {}, {
        runtime: "codex",
        thread_id: "thread-1",
        turn_id: "turn-1",
        seq: 87,
      }),
    );

    await store.getState().send("second");
    const oldTerminal = ev(
      "finished",
      {},
      {
        runtime: "codex",
        thread_id: "thread-1",
        turn_id: "turn-1",
        seq: 88,
      },
    );
    stream.apply!(oldTerminal);
    stream.apply!(oldTerminal);

    const session = store.getState().sessions.s1;
    expect(session.turns.at(-1)?.status).toBe("active");
    expect(session.turnState).toBe("unknown");
    expect(session.needsReplay).toBe(true);
  });

  it("does not turn an application stream disconnect into a terminal", async () => {
    const store = createAgentStore();
    mockCreate("s1", { runtime: "codex", capabilities: ["tools"] });
    await store.getState().startSession({ workdir: "/wd", runtime: "codex" });

    await store.getState().send("hello");
    await releaseAllStreams();
    await vi.waitFor(() =>
      expect(store.getState().sessions.s1.liveState).toBe("reconnecting"),
    );
    expect(store.getState().sessions.s1.turnState).toBe("unknown");
    expect(store.getState().sessions.s1.turns[0].status).toBe("active");
  });

  it("keeps live reconnecting after a successful snapshot taken while disconnected", async () => {
    const store = createAgentStore();
    const created = mockCreate("s1", { connected: true });
    await store.getState().startSession({ workdir: "/wd" });
    await store.getState().send("hello");
    listActiveSessions.mockResolvedValueOnce({
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
    });
    apiGetAgentHistory.mockResolvedValueOnce([]);

    await releaseAllStreams();
    await vi.waitFor(() => expect(apiGetAgentHistory).toHaveBeenCalledOnce());

    expect(store.getState().sessions.s1.liveState).toBe("reconnecting");
  });

  it("completes a local slash command on the backend synthetic terminal", async () => {
    const store = createAgentStore();
    mockCreate("s1");
    await store.getState().startSession({ workdir: "/wd" });

    await store.getState().send("/cost");
    stream.apply!(ev("local_command", { content: "cost: 0.1" }));
    stream.apply!(ev("finished", {}, { turn_id: "turn-1" }));

    const session = store.getState().sessions.s1;
    expect(session.phase).toBe("done");
    expect(session.turns[0].status).toBe("done");
  });

  it("completes a model restart on the backend synthetic terminal", async () => {
    const store = createAgentStore();
    mockCreate("s1");
    await store.getState().startSession({ workdir: "/wd" });

    await store.getState().send("/model opus");
    stream.apply!(ev("status", { stage: "restarting: model=opus" }));
    stream.apply!(ev("model_changed", { model: "opus", effort: null }));
    stream.apply!(ev("finished", {}, { turn_id: "turn-1" }));

    const session = store.getState().sessions.s1;
    expect(session.phase).toBe("done");
    expect(session.turns[0].status).toBe("done");
  });
});
