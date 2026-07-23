import { describe, it, expect } from "vitest";
import {
  reduceEvent,
  run,
  type TrowelEvent,
  installReducerTestReset,
} from "./support";

installReducerTestReset();

describe("reduceEvent — Codex mapping (post-adapter)", () => {
  it("Codex assistant_delta arrives as text and accumulates", () => {
    const state = run([
      { type: "user", text: "hi" },
      { type: "text", text: "hello " } as TrowelEvent,
      { type: "text", text: "world" } as TrowelEvent,
    ]);
    const last = state.turns[state.turns.length - 1];
    expect(last.items).toHaveLength(1);
    expect((last.items[0] as { text: string }).text).toBe("hello world");
  });

  it("Codex reasoning_delta arrives as thinking and stays separate from text", () => {
    const state = run([
      { type: "user", text: "hi" },
      { type: "text", text: "answer" } as TrowelEvent,
      { type: "thinking", text: "because " } as TrowelEvent,
      { type: "thinking", text: "reasons" } as TrowelEvent,
    ]);
    const items = state.turns[state.turns.length - 1].items;
    expect(items.map((i) => i.kind)).toEqual(["text", "thinking"]);
    expect((items[1] as { text: string }).text).toBe("because reasons");
  });

  it("Codex command tool_call→tool_result carries exit_code/duration/cwd", () => {
    const state = run([
      { type: "user", text: "pwd" },
      {
        type: "tool_call",
        tool_use_id: "cmd-1",
        tool_name: "command",
        input: { command: "pwd", cwd: "/repo" },
      } as TrowelEvent,
      {
        type: "tool_result",
        tool_use_id: "cmd-1",
        content: "/repo",
        exit_code: 0,
        duration_ms: 12,
        cwd: "/repo",
        status: "completed",
      } as TrowelEvent,
    ]);
    const tool = state.turns[0].items[0];
    expect(tool.kind).toBe("tool");
    if (tool.kind === "tool") {
      expect(tool.status).toBe("done");
      expect(tool.result).toBe("/repo");
      expect(tool.exitCode).toBe(0);
      expect(tool.durationMs).toBe(12);
      expect(tool.cwd).toBe("/repo");
      expect(tool.nativeStatus).toBe("completed");
    }
  });

  it("usage_updated stores token accounting on meta.usage", () => {
    const state = run([
      {
        type: "usage_updated",
        total: 25000,
        model_context_window: 200000,
      } as TrowelEvent,
    ]);
    expect(state.meta.usage).toEqual({
      total: 25000,
      last: null,
      model_context_window: 200000,
    });
  });

  it("host_status host_exited errors the running turn + flags degraded", () => {
    const state = run([
      { type: "user", text: "go" },
      {
        type: "host_status",
        status: "host_exited",
        reason: "eof",
      } as TrowelEvent,
    ]);
    expect(state.phase).toBe("error");
    expect(state.meta.hostDegraded).toBe(true);
    expect(state.turns[0].status).toBe("error");
  });

  it("host_status degraded flags without erroring a turn", () => {
    const state = run([
      { type: "host_status", status: "degraded" } as TrowelEvent,
    ]);
    expect(state.meta.hostDegraded).toBe(true);
    expect(state.phase).toBe("idle");
  });

  it("host_status ready clears the degraded flag", () => {
    const degraded = run([
      { type: "host_status", status: "degraded" } as TrowelEvent,
    ]);
    const recovered = reduceEvent(degraded, {
      type: "host_status",
      status: "ready",
    } as TrowelEvent);
    expect(recovered.meta.hostDegraded).toBe(false);
  });
});
