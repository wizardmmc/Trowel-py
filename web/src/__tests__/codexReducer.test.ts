/**
 * slice-072: codexReducer — pins the core Codex event → ReducerState mapping.
 * Pure + immutable; every case returns a new state via spread.
 */
import { describe, it, expect } from "vitest";

import { INITIAL_REDUCER_STATE } from "../stores/ccReducer";
import { reduceCodexEvent, type CodexEvent } from "../stores/codexReducer";

function codexEvent(
  type: string,
  payload: Record<string, unknown> = {},
  over: Partial<CodexEvent> = {},
): CodexEvent {
  return { type, payload, runtime: "codex", ...over };
}

describe("codexReducer", () => {
  it("session_started stamps model + thread id", () => {
    const next = reduceCodexEvent(
      INITIAL_REDUCER_STATE,
      codexEvent("session_started", { model: "gpt-5.6-sol" }, { thread_id: "th1" }),
    );
    expect(next.meta.model).toBe("gpt-5.6-sol");
    expect(next.meta.ccSessionId).toBe("th1");
  });

  it("user adds a turn carrying the echoed text", () => {
    const next = reduceCodexEvent(
      INITIAL_REDUCER_STATE,
      codexEvent("user", { text: "hello" }),
    );
    expect(next.turns).toHaveLength(1);
    expect(next.turns[0].userText).toBe("hello");
    expect(next.turns[0].status).toBe("active");
  });

  it("assistant_delta accumulates payload.delta into one TextItem", () => {
    let s = reduceCodexEvent(INITIAL_REDUCER_STATE, codexEvent("user", { text: "hi" }));
    s = reduceCodexEvent(s, codexEvent("assistant_delta", { delta: "Hello " }));
    s = reduceCodexEvent(s, codexEvent("assistant_delta", { delta: "world" }));
    const items = s.turns[0].items;
    expect(items).toHaveLength(1);
    expect(items[0]).toEqual({ kind: "text", text: "Hello world" });
    expect(s.phase).toBe("generating");
  });

  it("assistant_message is a no-op (deltas already accumulated the text)", () => {
    let s = reduceCodexEvent(INITIAL_REDUCER_STATE, codexEvent("user", { text: "hi" }));
    s = reduceCodexEvent(s, codexEvent("assistant_delta", { delta: "Hi" }));
    const before = s;
    s = reduceCodexEvent(s, codexEvent("assistant_message", { text: "Hi" }));
    expect(s).toBe(before);
  });

  it("tool_started then tool_completed marks the tool done with result", () => {
    let s = reduceCodexEvent(INITIAL_REDUCER_STATE, codexEvent("user", { text: "x" }));
    s = reduceCodexEvent(
      s,
      codexEvent("tool_started", { command: "ls -la" }, { item_id: "i1" }),
    );
    const tool = s.turns[0].items[0];
    expect(tool.kind).toBe("tool");
    if (tool.kind === "tool") {
      expect(tool.toolName).toBe("command");
      expect(tool.input).toEqual({ command: "ls -la" });
      expect(tool.status).toBe("running");
    }
    s = reduceCodexEvent(
      s,
      codexEvent("tool_completed", { output: "done-out" }, { item_id: "i1" }),
    );
    const done = s.turns[0].items[0];
    if (done.kind === "tool") {
      expect(done.status).toBe("done");
      expect(done.result).toBe("done-out");
    }
  });

  it("finished flips the turn + phase to done", () => {
    let s = reduceCodexEvent(INITIAL_REDUCER_STATE, codexEvent("user", { text: "x" }));
    s = reduceCodexEvent(s, codexEvent("finished"));
    expect(s.turns[0].status).toBe("done");
    expect(s.phase).toBe("done");
  });

  it("interrupted + error flip phase but keep the turn", () => {
    let s = reduceCodexEvent(INITIAL_REDUCER_STATE, codexEvent("user", { text: "x" }));
    s = reduceCodexEvent(s, codexEvent("interrupted"));
    expect(s.phase).toBe("interrupted");
    expect(s.turns[0].status).toBe("interrupted");
    s = reduceCodexEvent(s, codexEvent("error"));
    expect(s.phase).toBe("error");
  });

  it("host_status host_exited flips the running turn to error (row kept, spec §4)", () => {
    let s = reduceCodexEvent(
      INITIAL_REDUCER_STATE,
      codexEvent("user", { text: "x" }),
    );
    s = reduceCodexEvent(s, codexEvent("host_status", { status: "host_exited" }));
    expect(s.turns[0].status).toBe("error");
    expect(s.phase).toBe("error");
  });

  it("host_status ready is a no-op", () => {
    const before = INITIAL_REDUCER_STATE;
    const after = reduceCodexEvent(
      before,
      codexEvent("host_status", { status: "ready" }),
    );
    expect(after).toBe(before);
  });

  it("unknown event types are a no-op (slice-074 maps the rest)", () => {
    const before = INITIAL_REDUCER_STATE;
    const after = reduceCodexEvent(before, codexEvent("usage_updated", { total: 10 }));
    expect(after).toBe(before);
  });

  it("never mutates the input state", () => {
    const before = reduceCodexEvent(
      INITIAL_REDUCER_STATE,
      codexEvent("user", { text: "x" }),
    );
    const frozen = JSON.parse(JSON.stringify(before)) as object;
    reduceCodexEvent(before, codexEvent("assistant_delta", { text: "y" }));
    expect(JSON.parse(JSON.stringify(before))).toEqual(frozen);
  });
});
