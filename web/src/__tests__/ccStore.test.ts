import { describe, it, expect, beforeEach } from "vitest";
import {
  reduceEvent,
  INITIAL_REDUCER_STATE,
  _resetTurnIdCounterForTests,
} from "../stores/ccStore";
import type { TrowelEvent } from "../api/ccTypes";

beforeEach(() => {
  _resetTurnIdCounterForTests();
});

/** Run events through the reducer from a clean slate. */
function run(events: TrowelEvent[]) {
  let state = { ...INITIAL_REDUCER_STATE };
  for (const ev of events) {
    state = reduceEvent(state, ev);
  }
  return state;
}

/** Start with one open user turn (simulating the live optimistic append). */
function withOpenTurn(userText = "hi") {
  return reduceEvent(INITIAL_REDUCER_STATE, { type: "user", text: userText });
}

describe("reduceEvent — session_started", () => {
  it("fills model + cc_session_id from the init event", () => {
    const state = run([
      {
        type: "session_started",
        model: "glm-5.2",
        cwd: "/x",
        cc_session_id: "s1",
        tools: ["Read"],
      },
    ]);
    expect(state.meta.model).toBe("glm-5.2");
    expect(state.meta.ccSessionId).toBe("s1");
  });
});

describe("reduceEvent — text delta accumulation", () => {
  it("concatenates consecutive text deltas into one text item", () => {
    const state = reduceEvent(
      withOpenTurn(),
      { type: "text", text: "he" },
    );
    const state2 = reduceEvent(state, { type: "text", text: "llo" });
    const items = state2.turns[0].items;
    expect(items).toHaveLength(1);
    expect(items[0]).toMatchObject({ kind: "text", text: "hello" });
    expect(state2.phase).toBe("generating");
  });

  it("starts a new text item after a non-text item", () => {
    let state = withOpenTurn();
    state = reduceEvent(state, { type: "text", text: "a" });
    state = reduceEvent(state, { type: "tool_call", tool_use_id: "t1", tool_name: "Bash", input: {} });
    state = reduceEvent(state, { type: "text", text: "b" });
    const kinds = state.turns[0].items.map((i) => i.kind);
    expect(kinds).toEqual(["text", "tool", "text"]);
  });
});

describe("reduceEvent — thinking", () => {
  it("accumulates thinking deltas and sets phase thinking", () => {
    let state = withOpenTurn();
    state = reduceEvent(state, { type: "thinking", text: "reason " });
    state = reduceEvent(state, { type: "thinking", text: "more" });
    expect(state.phase).toBe("thinking");
    expect(state.turns[0].items[0]).toMatchObject({
      kind: "thinking",
      text: "reason more",
    });
  });
});

describe("reduceEvent — tool lifecycle", () => {
  it("tool_call -> tool_progress -> tool_result marks done with result", () => {
    let state = withOpenTurn();
    state = reduceEvent(state, {
      type: "tool_call",
      tool_use_id: "t1",
      tool_name: "Write",
      input: { file_path: "/a" },
    });
    state = reduceEvent(state, {
      type: "tool_progress",
      tool_use_id: "t1",
      tool_name: "Write",
      elapsed_time_seconds: 0.5,
    });
    state = reduceEvent(state, {
      type: "tool_result",
      tool_use_id: "t1",
      content: "wrote",
    });
    const tool = state.turns[0].items.find((i) => i.kind === "tool");
    expect(tool).toMatchObject({
      kind: "tool",
      toolName: "Write",
      status: "done",
      elapsedSeconds: 0.5,
      result: "wrote",
    });
    expect(state.phase).toBe("tool");
  });

  it("tool_progress for a different id does not touch the matched tool", () => {
    let state = withOpenTurn();
    state = reduceEvent(state, { type: "tool_call", tool_use_id: "t1", tool_name: "A", input: {} });
    state = reduceEvent(state, {
      type: "tool_progress",
      tool_use_id: "other",
      tool_name: "B",
      elapsed_time_seconds: 9,
    });
    const tool = state.turns[0].items.find((i) => i.kind === "tool");
    expect(tool).toMatchObject({ kind: "tool", elapsedSeconds: null });
  });
});

describe("reduceEvent — retrying / stalled / compact_boundary / hook / status", () => {
  it("retrying adds a sunshine-eligible item with attempt + delay", () => {
    const state = reduceEvent(withOpenTurn(), {
      type: "retrying",
      attempt: 1,
      max_retries: 5,
      error_status: 529,
      error: "overloaded",
      retry_delay_ms: 2000,
    });
    expect(state.phase).toBe("retrying");
    expect(state.turns[0].items[0]).toMatchObject({
      kind: "retrying",
      attempt: 1,
      retryDelayMs: 2000,
      errorStatus: 529,
    });
  });

  it("stalled sets phase stalled and adds a stalled item", () => {
    const state = reduceEvent(withOpenTurn(), { type: "stalled" });
    expect(state.phase).toBe("stalled");
    expect(state.turns[0].items[0]).toMatchObject({ kind: "stalled" });
  });

  it("status compacting sets phase compacting", () => {
    const state = reduceEvent(withOpenTurn(), { type: "status", stage: "compacting" });
    expect(state.phase).toBe("compacting");
  });

  it("compact_boundary adds a divider item", () => {
    const state = reduceEvent(withOpenTurn(), { type: "compact_boundary" });
    expect(state.turns[0].items[0]).toMatchObject({ kind: "compact_boundary" });
  });

  it("hook records hook_name on meta for the StatusBar chip", () => {
    const state = run([{ type: "hook", hook_name: "SessionStart", outcome: "ok" }]);
    expect(state.meta.hookFired).toBe("SessionStart");
  });
});

describe("reduceEvent — terminal events", () => {
  it("finished sets cost + num_turns and phase done", () => {
    const state = reduceEvent(withOpenTurn(), {
      type: "finished",
      usage: {},
      total_cost_usd: 0.04,
      num_turns: 2,
    });
    expect(state.phase).toBe("done");
    expect(state.meta.costUsd).toBe(0.04);
    expect(state.meta.numTurns).toBe(2);
  });

  it("finished also flips the current turn status to done", () => {
    const state = reduceEvent(withOpenTurn(), {
      type: "finished",
      usage: {},
      total_cost_usd: 0,
      num_turns: 1,
    });
    expect(state.turns[0].status).toBe("done");
  });

  it("error adds an error item with subclass and marks turn status error", () => {
    const state = reduceEvent(withOpenTurn(), {
      type: "error",
      subclass: "error_max_turns",
      errors: ["loop"],
      api_error_status: null,
    });
    expect(state.phase).toBe("error");
    expect(state.turns[0].status).toBe("error");
    expect(state.turns[0].items[0]).toMatchObject({
      kind: "error",
      subclass: "error_max_turns",
    });
  });

  it("interrupted adds a soft-transition item and keeps input usable (phase interrupted)", () => {
    const state = reduceEvent(withOpenTurn(), { type: "interrupted" });
    expect(state.phase).toBe("interrupted");
    expect(state.turns[0].status).toBe("interrupted");
    expect(state.turns[0].items[0]).toMatchObject({ kind: "interrupted" });
  });
});

describe("reduceEvent — history replay via user event", () => {
  it("a full history sequence renders user + assistant text in one reducer", () => {
    const state = run([
      { type: "user", text: "请回数字 1" },
      { type: "text", text: "1" },
      { type: "finished", usage: {}, total_cost_usd: 0.001, num_turns: 1 },
    ]);
    expect(state.turns).toHaveLength(1);
    expect(state.turns[0].userText).toBe("请回数字 1");
    expect(state.turns[0].items.map((i) => i.kind)).toEqual(["text"]);
    expect(state.phase).toBe("done");
  });

  it("two user turns in history produce two turns", () => {
    const state = run([
      { type: "user", text: "q1" },
      { type: "text", text: "a1" },
      { type: "finished", usage: {}, total_cost_usd: 0, num_turns: 1 },
      { type: "user", text: "q2" },
      { type: "text", text: "a2" },
    ]);
    expect(state.turns.map((t) => t.userText)).toEqual(["q1", "q2"]);
  });
});

describe("reduceEvent — immutability", () => {
  it("never mutates the previous state", () => {
    const before = withOpenTurn();
    const snapshot = JSON.stringify(before);
    reduceEvent(before, { type: "text", text: "x" });
    reduceEvent(before, { type: "finished", usage: {}, total_cost_usd: 0, num_turns: 0 });
    expect(JSON.stringify(before)).toBe(snapshot);
  });
});
