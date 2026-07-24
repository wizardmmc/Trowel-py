import { describe, it, expect } from "vitest";
import {
  reduceEvent,
  run,
  withOpenTurn,
  installReducerTestReset,
} from "./support";

installReducerTestReset();

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

describe("reduceEvent — model_changed", () => {
  it("updates meta.model from event.model", () => {
    const started = run([
      {
        type: "session_started",
        model: "sonnet",
        cwd: "/wd",
        cc_session_id: "s1",
        tools: [],
      },
    ]);
    const next = reduceEvent(started, {
      type: "model_changed",
      model: "opus",
      effort: null,
    });
    expect(next.meta.model).toBe("opus");
  });

  it("keeps previous model when event.model is null (follow settings)", () => {
    const started = run([
      {
        type: "session_started",
        model: "sonnet",
        cwd: "/wd",
        cc_session_id: "s1",
        tools: [],
      },
    ]);
    const next = reduceEvent(started, {
      type: "model_changed",
      model: null,
      effort: "high",
    });
    expect(next.meta.model).toBe("sonnet");
  });

  it("is a no-op (same ref) when model unchanged", () => {
    const started = run([
      {
        type: "session_started",
        model: "opus",
        cwd: "/wd",
        cc_session_id: "s1",
        tools: [],
      },
    ]);
    const next = reduceEvent(started, {
      type: "model_changed",
      model: "opus",
      effort: null,
    });
    expect(next).toBe(started);
  });
});

describe("reduceEvent — text delta accumulation", () => {
  it("concatenates consecutive text deltas into one text item", () => {
    const state = reduceEvent(withOpenTurn(), { type: "text", text: "he" });
    const state2 = reduceEvent(state, { type: "text", text: "llo" });
    const items = state2.turns[0].items;
    expect(items).toHaveLength(1);
    expect(items[0]).toMatchObject({ kind: "text", text: "hello" });
    expect(state2.phase).toBe("generating");
  });

  it("starts a new text item after a non-text item", () => {
    let state = withOpenTurn();
    state = reduceEvent(state, { type: "text", text: "a" });
    state = reduceEvent(state, {
      type: "tool_call",
      tool_use_id: "t1",
      tool_name: "Bash",
      input: {},
    });
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

  it("tool_result carries write_diff -> merged onto ToolItem", () => {
    let state = withOpenTurn();
    state = reduceEvent(state, {
      type: "tool_call",
      tool_use_id: "t1",
      tool_name: "Edit",
      input: { file_path: "/a", old_string: "x", new_string: "y" },
    });
    state = reduceEvent(state, {
      type: "tool_result",
      tool_use_id: "t1",
      content: "updated",
      write_diff: {
        type: "update",
        hunks: [
          {
            oldStart: 360,
            oldLines: 1,
            newStart: 360,
            newLines: 1,
            lines: ["-x", "+y"],
          },
        ],
      },
    });
    const tool = state.turns[0].items.find((i) => i.kind === "tool") as {
      writeDiff?: { type: string; hunks: { oldStart: number }[] };
    };
    expect(tool?.writeDiff).toBeDefined();
    expect(tool?.writeDiff?.type).toBe("update");
    expect(tool?.writeDiff?.hunks[0]?.oldStart).toBe(360);
  });

  it("codex apply_patch tool_result carries write_diff onto ToolItem", () => {
    let state = withOpenTurn();
    state = reduceEvent(state, {
      type: "tool_call",
      tool_use_id: "fc-1",
      tool_name: "apply_patch",
      input: { paths: ["/repo/greeting.txt"], change_kinds: ["modify"] },
    });
    state = reduceEvent(state, {
      type: "tool_result",
      tool_use_id: "fc-1",
      content: null,
      write_diff: {
        type: "update",
        hunks: [
          {
            oldStart: 1,
            oldLines: 1,
            newStart: 1,
            newLines: 1,
            lines: ["-hi", "+hey"],
          },
        ],
      },
      status: "completed",
    });
    const tool = state.turns[0].items.find((i) => i.kind === "tool") as {
      toolName?: string;
      writeDiff?: { type: string; hunks: { lines: string[] }[] };
      nativeStatus?: string;
    };
    expect(tool?.toolName).toBe("apply_patch");
    expect(tool?.writeDiff?.type).toBe("update");
    expect(tool?.writeDiff?.hunks[0]?.lines).toEqual(["-hi", "+hey"]);
    expect(tool?.nativeStatus).toBe("completed");
  });

  it("codex apply_patch declined tool_result keeps declined nativeStatus", () => {
    let state = withOpenTurn();
    state = reduceEvent(state, {
      type: "tool_call",
      tool_use_id: "fc-2",
      tool_name: "apply_patch",
      input: { paths: ["/repo/x.txt"], change_kinds: ["add"] },
    });
    state = reduceEvent(state, {
      type: "tool_result",
      tool_use_id: "fc-2",
      content: null,
      write_diff: { type: "create", hunks: [] },
      status: "declined",
    });
    const tool = state.turns[0].items.find((i) => i.kind === "tool") as {
      toolName?: string;
      nativeStatus?: string;
      status?: string;
    };
    expect(tool?.toolName).toBe("apply_patch");
    expect(tool?.nativeStatus).toBe("declined");
    expect(tool?.status).toBe("failed");
  });

  it("tool_result WITHOUT write_diff leaves writeDiff undefined", () => {
    let state = withOpenTurn();
    state = reduceEvent(state, {
      type: "tool_call",
      tool_use_id: "t1",
      tool_name: "Bash",
      input: { command: "echo hi" },
    });
    state = reduceEvent(state, {
      type: "tool_result",
      tool_use_id: "t1",
      content: "hi",
    });
    const tool = state.turns[0].items.find((i) => i.kind === "tool") as {
      writeDiff?: unknown;
    };
    expect(tool?.writeDiff).toBeUndefined();
  });

  it("tool_progress for a different id does not touch the matched tool", () => {
    let state = withOpenTurn();
    state = reduceEvent(state, {
      type: "tool_call",
      tool_use_id: "t1",
      tool_name: "A",
      input: {},
    });
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
