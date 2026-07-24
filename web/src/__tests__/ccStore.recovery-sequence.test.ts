import { describe, expect, it } from "vitest";
import {
  apiAnswerAgentRequest,
  ev,
  mockCreate,
  releaseAllStreams,
  stream,
} from "./ccStoreTestHarness";
import { createCcStore } from "../stores/ccStore";

describe("createCcStore — approval recovery", () => {
  it("folds the answer response into the pending card when SSE is unavailable", async () => {
    const store = createCcStore();
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

describe("createCcStore — per-session event sequence", () => {
  it("drops a duplicate seq (re-delivered event does not double-append)", async () => {
    const store = createCcStore();
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
    const store = createCcStore();
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
    const store = createCcStore();
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
});
