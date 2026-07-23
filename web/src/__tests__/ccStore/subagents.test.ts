import { describe, it, expect } from "vitest";
import { reduceEvent, withOpenTurn, installReducerTestReset } from "./support";

installReducerTestReset();

describe("reduceEvent — subagent_progress", () => {
  it("attaches to the matching Agent ToolItem and merges fields across events", () => {
    let state = withOpenTurn();
    state = reduceEvent(state, {
      type: "tool_call",
      tool_use_id: "call_1",
      tool_name: "Agent",
      input: {},
    });
    state = reduceEvent(state, {
      type: "subagent_progress",
      tool_use_id: "call_1",
      task_id: "t1",
      status: "started",
      description: "Count files",
      subagent_type: "general-purpose",
    });
    state = reduceEvent(state, {
      type: "subagent_progress",
      tool_use_id: "call_1",
      task_id: "t1",
      status: "progress",
      last_tool_name: "Bash",
      usage: { total_tokens: 10 },
    });
    const item = state.turns[0].items[0];
    expect(item.kind).toBe("tool");
    if (item.kind === "tool") {
      expect(item.subagent?.status).toBe("progress");
      expect(item.subagent?.description).toBe("Count files");
      expect(item.subagent?.subagent_type).toBe("general-purpose");
      expect(item.subagent?.last_tool_name).toBe("Bash");
      expect(item.subagent?.usage).toEqual({ total_tokens: 10 });
    }
  });

  it("marks the Agent tool completed on task_notification", () => {
    let state = withOpenTurn();
    state = reduceEvent(state, {
      type: "tool_call",
      tool_use_id: "call_1",
      tool_name: "Agent",
      input: {},
    });
    state = reduceEvent(state, {
      type: "subagent_progress",
      tool_use_id: "call_1",
      task_id: "t1",
      status: "completed",
      usage: { total_tokens: 0, tool_uses: 2 },
    });
    const item = state.turns[0].items[0];
    if (item.kind === "tool") {
      expect(item.subagent?.status).toBe("completed");
      expect(item.subagent?.usage).toEqual({ total_tokens: 0, tool_uses: 2 });
    }
  });

  it("task_started → task_notification → finished keeps one turn + done phase", () => {
    let state = withOpenTurn();
    state = reduceEvent(state, {
      type: "subagent_progress",
      tool_use_id: "call_bg",
      task_id: "b7cgk2tn3",
      status: "started",
      description: "sleep 6",
    });
    expect(state.phase).not.toBe("done");
    state = reduceEvent(state, {
      type: "status",
      stage: "background_waiting",
    });
    expect(state.phase).toBe("background_waiting");
    expect(state.turns[0].status).toBe("active");
    state = reduceEvent(state, {
      type: "subagent_progress",
      tool_use_id: "call_bg",
      task_id: "b7cgk2tn3",
      status: "completed",
      usage: { total_tokens: 0 },
    });
    expect(state.phase).not.toBe("done");
    expect(state.turns.length).toBe(1);
    state = reduceEvent(state, {
      type: "finished",
      total_cost_usd: 0.02,
      usage: {},
      num_turns: 2,
    });
    expect(state.phase).toBe("done");
    expect(state.turns.length).toBe(1);
  });

  it("falls back to a standalone subagent item when no Agent tool matches", () => {
    const state = reduceEvent(withOpenTurn(), {
      type: "subagent_progress",
      tool_use_id: "orphan",
      task_id: "t1",
      status: "started",
      description: "lonely",
    });
    expect(state.turns[0].items[0].kind).toBe("subagent");
  });

  it("does not attach to a non-Agent tool with the same id", () => {
    let state = withOpenTurn();
    state = reduceEvent(state, {
      type: "tool_call",
      tool_use_id: "x",
      tool_name: "Bash",
      input: {},
    });
    state = reduceEvent(state, {
      type: "subagent_progress",
      tool_use_id: "x",
      task_id: "t1",
      status: "started",
      description: "d",
    });
    expect(state.turns[0].items.map((i) => i.kind)).toEqual([
      "tool",
      "subagent",
    ]);
  });

  it("attaches to a NESTED Agent (subagent calling subagent), not a top-level standalone row", () => {
    // 进度匹配必须递归 childTools；只查顶层会把每次更新都溢出成独立行。
    let state = withOpenTurn("use agent");
    state = reduceEvent(state, {
      type: "tool_call",
      tool_use_id: "outer",
      tool_name: "Agent",
      input: { description: "outer", subagent_type: "general-purpose" },
    });
    state = reduceEvent(state, {
      type: "tool_call",
      tool_use_id: "inner",
      tool_name: "Agent",
      input: { description: "inner", subagent_type: "general-purpose" },
      parent_tool_use_id: "outer",
    });
    state = reduceEvent(state, {
      type: "subagent_progress",
      tool_use_id: "inner",
      task_id: "t-inner",
      status: "started",
      description: "inner work",
    });
    state = reduceEvent(state, {
      type: "subagent_progress",
      tool_use_id: "inner",
      task_id: "t-inner",
      status: "progress",
      last_tool_name: "Bash",
      usage: { total_tokens: 5 },
    });

    const top = state.turns[0].items;
    expect(top).toHaveLength(1);
    expect(top[0].kind).toBe("tool");
    if (top[0].kind === "tool") {
      expect(top[0].childTools).toHaveLength(1);
      const inner = top[0].childTools[0];
      expect(inner.toolUseId).toBe("inner");
      expect(inner.subagent?.status).toBe("progress");
      expect(inner.subagent?.description).toBe("inner work");
      expect(inner.subagent?.last_tool_name).toBe("Bash");
      expect(inner.subagent?.usage).toEqual({ total_tokens: 5 });
    }
  });

  it("does not attach to a non-Agent tool with the same id even when nested in childTools", () => {
    let state = withOpenTurn("agent with bash child");
    state = reduceEvent(state, {
      type: "tool_call",
      tool_use_id: "agent-1",
      tool_name: "Agent",
      input: {},
    });
    state = reduceEvent(state, {
      type: "tool_call",
      tool_use_id: "bash-x",
      tool_name: "Bash",
      input: {},
      parent_tool_use_id: "agent-1",
    });
    state = reduceEvent(state, {
      type: "subagent_progress",
      tool_use_id: "bash-x",
      task_id: "t1",
      status: "started",
      description: "should not merge into Bash",
    });
    const top = state.turns[0].items;
    expect(top.map((i) => i.kind)).toEqual(["tool", "subagent"]);
    if (top[0].kind === "tool") {
      expect(top[0].childTools).toHaveLength(1);
      expect(top[0].childTools[0].toolName).toBe("Bash");
      expect(top[0].childTools[0].subagent).toBeUndefined();
    }
  });

  it("merges subagent_progress for a deeply nested Agent (3 levels)", () => {
    let state = withOpenTurn("deep");
    state = reduceEvent(state, {
      type: "tool_call",
      tool_use_id: "outer",
      tool_name: "Agent",
      input: {},
    });
    state = reduceEvent(state, {
      type: "tool_call",
      tool_use_id: "middle",
      tool_name: "Agent",
      input: {},
      parent_tool_use_id: "outer",
    });
    state = reduceEvent(state, {
      type: "tool_call",
      tool_use_id: "inner",
      tool_name: "Agent",
      input: {},
      parent_tool_use_id: "middle",
    });
    state = reduceEvent(state, {
      type: "subagent_progress",
      tool_use_id: "inner",
      task_id: "ti",
      status: "completed",
      usage: { total_tokens: 0, tool_uses: 3 },
    });
    const top = state.turns[0].items;
    expect(top).toHaveLength(1);
    expect(top[0].kind).toBe("tool");
    if (top[0].kind === "tool") {
      const middle = top[0].childTools[0];
      expect(middle.toolUseId).toBe("middle");
      const inner = middle.childTools[0];
      expect(inner.toolUseId).toBe("inner");
      expect(inner.subagent?.status).toBe("completed");
      expect(inner.subagent?.usage).toEqual({ total_tokens: 0, tool_uses: 3 });
    }
  });
});
