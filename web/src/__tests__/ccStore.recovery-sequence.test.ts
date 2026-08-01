import { describe, expect, it } from "vitest";
import {
  apiAnswerAgentRequest,
  ev,
  mockCreate,
  releaseAllStreams,
  stream,
} from "./ccStoreTestHarness";
import { createAgentStore } from "../agent";

describe("createAgentStore — approval recovery", () => {
  it("folds the answer response into the pending card when SSE is unavailable", async () => {
    const store = createAgentStore();
    mockCreate("s1", {
      runtime: "codex",
      model: "gpt-5.6-sol",
      capabilities: ["tools", "approval"],
    });
    await store.getState().startSession({ workdir: "/wd", runtime: "codex" });
    const sending = store.getState().send("run it");
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
    await releaseAllStreams();
    await sending;
  });
});

describe("createAgentStore — per-session event sequence", () => {
  it("drops a duplicate seq (re-delivered event does not double-append)", async () => {
    const store = createAgentStore();
    mockCreate("s1");
    await store.getState().startSession({ workdir: "/wd" });
    const sending = store.getState().send("hi");
    stream.apply!(ev("text", { text: "a" }));
    stream.apply!(ev("text", { text: "b" }, { seq: 1 }));
    stream.apply!(ev("finished"));
    await releaseAllStreams();
    await sending;
    const turn = store.getState().sessions.s1.turns[0];
    expect(turn.items.filter((item) => item.kind === "text")).toHaveLength(1);
    expect((turn.items[0] as { text: string }).text).toBe("a");
  });

  it("flags needsReplay when a seq gap is observed", async () => {
    const store = createAgentStore();
    mockCreate("s1");
    await store.getState().startSession({ workdir: "/wd" });
    const sending = store.getState().send("hi");
    stream.apply!(ev("text", { text: "a" }));
    stream.apply!(ev("text", { text: "b" }, { seq: 5 }));
    stream.apply!(ev("finished"));
    await releaseAllStreams();
    await sending;
    expect(store.getState().sessions.s1.needsReplay).toBe(true);
  });

  it("seq is per-session (two streams do not share the counter)", async () => {
    const store = createAgentStore();
    mockCreate("s1");
    await store.getState().startSession({ workdir: "/a" });
    const first = store.getState().send("one");
    stream.apply!(ev("text", { text: "a" }));
    stream.apply!(ev("finished"));
    await releaseAllStreams();
    await first;
    mockCreate("s2");
    await store.getState().startSession({ workdir: "/b" });
    const second = store.getState().send("two");
    stream.apply!(ev("text", { text: "b" }, { seq: 1, session_id: "s2" }));
    stream.apply!(ev("finished", {}, { seq: 2, session_id: "s2" }));
    await releaseAllStreams();
    await second;
    expect(store.getState().sessions.s1.lastSeq).toBe(2);
    expect(store.getState().sessions.s2.lastSeq).toBe(2);
    expect(store.getState().sessions.s2.needsReplay).toBe(false);
  });

  it("applies the current request error when a restarted backend resets seq", async () => {
    const store = createAgentStore();
    mockCreate("s1", { runtime: "codex", capabilities: ["tools"] });
    await store.getState().startSession({ workdir: "/wd", runtime: "codex" });

    const first = store.getState().send("first");
    stream.apply!(ev("finished", {}, { runtime: "codex", seq: 87 }));
    await releaseAllStreams();
    await first;

    const second = store.getState().send("please commit");
    stream.apply!(
      ev(
        "error",
        {
          subclass: "host_error",
          errors: ["codex session s1 not live"],
          api_error_status: null,
        },
        { runtime: "codex", seq: 1 },
      ),
    );
    await releaseAllStreams();
    await second;

    const session = store.getState().sessions.s1;
    const turn = session.turns.at(-1)!;
    expect(session.phase).toBe("error");
    expect(turn.status).toBe("error");
    expect(turn.items.at(-1)).toMatchObject({
      kind: "error",
      subclass: "host_error",
      errors: ["codex session s1 not live"],
    });
    expect(session.lastSeq).toBe(1);
  });

  it("still flags a seq gap when the current request error moves forward", async () => {
    const store = createAgentStore();
    mockCreate("s1", { runtime: "codex", capabilities: ["tools"] });
    await store.getState().startSession({ workdir: "/wd", runtime: "codex" });

    const sending = store.getState().send("hello");
    stream.apply!(ev("text", { text: "partial" }, { runtime: "codex", seq: 1 }));
    stream.apply!(
      ev(
        "error",
        {
          subclass: "host_error",
          errors: ["stream failed"],
          api_error_status: null,
        },
        { runtime: "codex", seq: 3 },
      ),
    );
    await releaseAllStreams();
    await sending;

    const session = store.getState().sessions.s1;
    expect(session.phase).toBe("error");
    expect(session.lastSeq).toBe(3);
    expect(session.needsReplay).toBe(true);
  });

  it("drops a repeated low-seq request error after accepting the reset", async () => {
    const store = createAgentStore();
    mockCreate("s1", { runtime: "codex", capabilities: ["tools"] });
    await store.getState().startSession({ workdir: "/wd", runtime: "codex" });

    const first = store.getState().send("first");
    stream.apply!(ev("finished", {}, { runtime: "codex", seq: 87 }));
    await releaseAllStreams();
    await first;

    const second = store.getState().send("second");
    const error = ev(
      "error",
      {
        subclass: "host_error",
        errors: ["codex session s1 not live"],
        api_error_status: null,
      },
      { runtime: "codex", seq: 1 },
    );
    stream.apply!(error);
    stream.apply!(error);
    await releaseAllStreams();
    await second;

    const items = store.getState().sessions.s1.turns.at(-1)!.items;
    expect(items.filter((item) => item.kind === "error")).toHaveLength(1);
  });

  it("turns a clean empty stream into a visible protocol error", async () => {
    const store = createAgentStore();
    mockCreate("s1", { runtime: "codex", capabilities: ["tools"] });
    await store.getState().startSession({ workdir: "/wd", runtime: "codex" });

    const sending = store.getState().send("hello");
    await releaseAllStreams();
    await sending;

    const session = store.getState().sessions.s1;
    expect(session.phase).toBe("error");
    expect(session.turns[0].status).toBe("error");
    expect(session.turns[0].items.at(-1)).toMatchObject({
      kind: "error",
      subclass: "stream_closed_without_terminal",
    });
  });

  it("still completes a local slash command without a finished event", async () => {
    const store = createAgentStore();
    mockCreate("s1");
    await store.getState().startSession({ workdir: "/wd" });

    const sending = store.getState().send("/cost");
    stream.apply!(ev("local_command", { content: "cost: 0.1" }));
    await releaseAllStreams();
    await sending;

    const session = store.getState().sessions.s1;
    expect(session.phase).toBe("done");
    expect(session.turns[0].status).toBe("done");
  });

  it("still completes a model restart status without a finished event", async () => {
    const store = createAgentStore();
    mockCreate("s1");
    await store.getState().startSession({ workdir: "/wd" });

    const sending = store.getState().send("/model opus");
    stream.apply!(ev("status", { stage: "restarting: model=opus" }));
    stream.apply!(ev("model_changed", { model: "opus", effort: null }));
    await releaseAllStreams();
    await sending;

    const session = store.getState().sessions.s1;
    expect(session.phase).toBe("done");
    expect(session.turns[0].status).toBe("done");
  });
});
