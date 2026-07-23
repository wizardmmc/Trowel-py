import { describe, it, expect } from "vitest";
import {
  reduceEvent,
  INITIAL_REDUCER_STATE,
  run,
  withOpenTurn,
  type TrowelEvent,
  installReducerTestReset,
} from "./support";

installReducerTestReset();

describe("reduceEvent — Codex live/history boundaries", () => {
  it("Codex live user-echo merges into the optimistic turn (no double turn)", () => {
    const optimistic = run([{ type: "user", text: "hi" }]);
    expect(optimistic.turns).toHaveLength(1);
    const afterEcho = reduceEvent(optimistic, { type: "user", text: "hi" });
    expect(afterEcho.turns).toHaveLength(1);
  });

  it("history user event still appends when no optimistic turn exists", () => {
    const state = run([
      { type: "user", text: "first" },
      { type: "user", text: "second" },
    ]);
    expect(state.turns).toHaveLength(2);
    expect(state.turns[0].userText).toBe("first");
    expect(state.turns[1].userText).toBe("second");
  });

  it("turn_start keeps the optimistic turnId when the event omits one", () => {
    const withOptimistic = reduceEvent(
      {
        ...INITIAL_REDUCER_STATE,
        turns: [
          {
            id: "turn-7",
            userText: "hi",
            items: [],
            status: "active",
            turnId: "native-9",
            revertible: false,
          },
        ],
      },
      {
        type: "turn_start",
        turn_id: undefined,
        revertible: false,
      } as unknown as TrowelEvent,
    );
    expect(withOptimistic.turns[0].turnId).toBe("native-9");
  });
});

describe("reduceEvent — Codex command failure", () => {
  it("tool_result with nativeStatus failed → ToolItem status failed", () => {
    const state = run([
      { type: "user", text: "go" },
      {
        type: "tool_call",
        tool_use_id: "c1",
        tool_name: "command",
        input: { command: "make" },
      } as TrowelEvent,
      {
        type: "tool_result",
        tool_use_id: "c1",
        content: "err",
        exit_code: 2,
        native_status: "failed",
      } as TrowelEvent,
    ]);
    const tool = state.turns[0].items[0];
    if (tool.kind === "tool") {
      expect(tool.status).toBe("failed");
      expect(tool.exitCode).toBe(2);
    }
  });

  it("tool_result with exit_code 0 + no nativeStatus → done", () => {
    const state = run([
      { type: "user", text: "go" },
      {
        type: "tool_call",
        tool_use_id: "c1",
        tool_name: "command",
        input: { command: "pwd" },
      } as TrowelEvent,
      {
        type: "tool_result",
        tool_use_id: "c1",
        content: "/r",
        exit_code: 0,
      } as TrowelEvent,
    ]);
    const tool = state.turns[0].items[0];
    if (tool.kind === "tool") expect(tool.status).toBe("done");
  });
});

describe("reduceEvent — Codex approval lifecycle", () => {
  it("upserts the same inline request from pending to answered", () => {
    let state = withOpenTurn();
    state = reduceEvent(state, {
      type: "approval_request",
      turn_id: "turn-1",
      request_id: "7-0",
      item_id: "exec-1",
      approval_kind: "command_approval",
      command: "printf PENDING",
      cwd: "/tmp/workspace",
      reason: "Allow it?",
      available_decisions: ["accept", "cancel"],
      status: "pending",
      decision: null,
      auto_resolved: false,
      resolution_reason: null,
    });
    expect(state.phase).toBe("awaiting_input");
    expect(state.turns[0].items).toHaveLength(1);
    expect(state.turns[0].items[0]).toMatchObject({
      kind: "approval",
      requestId: "7-0",
      status: "pending",
    });

    state = reduceEvent(state, {
      type: "approval_request",
      turn_id: "turn-1",
      request_id: "7-0",
      item_id: "exec-1",
      approval_kind: "command_approval",
      command: "printf PENDING",
      cwd: "/tmp/workspace",
      reason: "Allow it?",
      available_decisions: ["accept", "cancel"],
      status: "answered",
      decision: "accept",
      auto_resolved: false,
      resolution_reason: null,
    });
    expect(state.phase).toBe("tool");
    expect(state.turns[0].items).toHaveLength(1);
    expect(state.turns[0].items[0]).toMatchObject({
      kind: "approval",
      requestId: "7-0",
      status: "answered",
      decision: "accept",
    });
  });

  it("updates a recovered request in its original turn without duplicating it", () => {
    let state = withOpenTurn("first");
    state = reduceEvent(state, {
      type: "turn_start",
      turn_id: "turn-1",
      revertible: false,
    });
    state = reduceEvent(state, {
      type: "approval_request",
      turn_id: "turn-1",
      request_id: "7-0",
      item_id: "exec-1",
      approval_kind: "command_approval",
      command: "pwd",
      cwd: "/tmp",
      reason: "Allow it?",
      available_decisions: ["accept", "cancel"],
      status: "pending",
      decision: null,
      auto_resolved: false,
      resolution_reason: null,
    });
    state = reduceEvent(state, {
      type: "finished",
      usage: {},
      total_cost_usd: 0,
      num_turns: 1,
    });
    state = reduceEvent(state, { type: "user", text: "second" });

    state = reduceEvent(state, {
      type: "approval_request",
      turn_id: "turn-1",
      request_id: "7-0",
      item_id: "exec-1",
      approval_kind: "command_approval",
      command: "pwd",
      cwd: "/tmp",
      reason: "Allow it?",
      available_decisions: ["accept", "cancel"],
      status: "host_closed",
      decision: null,
      auto_resolved: false,
      resolution_reason: "host exited",
    });

    expect(state.turns[0].items).toHaveLength(1);
    expect(state.turns[0].items[0]).toMatchObject({
      kind: "approval",
      requestId: "7-0",
      status: "host_closed",
    });
    expect(state.turns[1].items).toHaveLength(0);
  });
});
