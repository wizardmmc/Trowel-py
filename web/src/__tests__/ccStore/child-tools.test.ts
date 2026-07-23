import { describe, it, expect } from "vitest";
import { reduceEvent, withOpenTurn, installReducerTestReset } from "./support";

installReducerTestReset();

describe("reduceEvent — sub-agent childTools attach", () => {
  it("tool_call with parent_tool_use_id attaches to the Agent ToolItem's childTools, not top-level", () => {
    let state = withOpenTurn("use agent");
    state = reduceEvent(state, {
      type: "tool_call",
      tool_use_id: "agent-1",
      tool_name: "Agent",
      input: { description: "count", subagent_type: "general-purpose" },
    });
    state = reduceEvent(state, {
      type: "tool_call",
      tool_use_id: "child-bash",
      tool_name: "Bash",
      input: {},
      parent_tool_use_id: "agent-1",
    });
    const tools = state.turns[0].items.filter((i) => i.kind === "tool");
    expect(tools).toHaveLength(1);
    const agent = tools[0];
    if (agent.kind !== "tool") throw new Error("expected tool");
    expect(agent.toolName).toBe("Agent");
    expect(agent.toolUseId).toBe("agent-1");
    expect(agent.childTools).toHaveLength(1);
    expect(agent.childTools[0].toolName).toBe("Bash");
    expect(agent.childTools[0].toolUseId).toBe("child-bash");
    expect(agent.childTools[0].childTools).toEqual([]);
  });

  it("tool_call without parent_tool_use_id stays top-level with empty childTools", () => {
    let state = withOpenTurn("hi");
    state = reduceEvent(state, {
      type: "tool_call",
      tool_use_id: "t1",
      tool_name: "Bash",
      input: {},
    });
    const tools = state.turns[0].items.filter((i) => i.kind === "tool");
    expect(tools).toHaveLength(1);
    if (tools[0].kind !== "tool") throw new Error("expected tool");
    expect(tools[0].childTools).toEqual([]);
  });

  it("tool_call with parent_tool_use_id but no matching Agent falls back to top-level", () => {
    let state = withOpenTurn("hi");
    state = reduceEvent(state, {
      type: "tool_call",
      tool_use_id: "orphan",
      tool_name: "Bash",
      input: {},
      parent_tool_use_id: "nonexistent",
    });
    const tools = state.turns[0].items.filter((i) => i.kind === "tool");
    expect(tools).toHaveLength(1);
    if (tools[0].kind !== "tool") throw new Error("expected tool");
    expect(tools[0].toolUseId).toBe("orphan");
    expect(tools[0].childTools).toEqual([]);
  });

  it("multi-level: grandchild nests under child which nests under Agent", () => {
    let state = withOpenTurn("nested");
    state = reduceEvent(state, {
      type: "tool_call",
      tool_use_id: "agent-1",
      tool_name: "Agent",
      input: {},
    });
    state = reduceEvent(state, {
      type: "tool_call",
      tool_use_id: "child-agent",
      tool_name: "Agent",
      input: {},
      parent_tool_use_id: "agent-1",
    });
    state = reduceEvent(state, {
      type: "tool_call",
      tool_use_id: "grand-bash",
      tool_name: "Bash",
      input: {},
      parent_tool_use_id: "child-agent",
    });
    const agent = state.turns[0].items.find((i) => i.kind === "tool");
    if (agent?.kind !== "tool") throw new Error("no agent");
    expect(agent.childTools).toHaveLength(1);
    expect(agent.childTools[0].toolUseId).toBe("child-agent");
    expect(agent.childTools[0].childTools).toHaveLength(1);
    expect(agent.childTools[0].childTools[0].toolUseId).toBe("grand-bash");
  });
});

describe("reduceEvent — childTools progress/result recursive update", () => {
  function setupWithChild() {
    let state = withOpenTurn("agent task");
    state = reduceEvent(state, {
      type: "tool_call",
      tool_use_id: "agent-1",
      tool_name: "Agent",
      input: {},
    });
    state = reduceEvent(state, {
      type: "tool_call",
      tool_use_id: "child-bash",
      tool_name: "Bash",
      input: {},
      parent_tool_use_id: "agent-1",
    });
    return state;
  }

  it("tool_progress updates the child tool inside childTools", () => {
    let state = setupWithChild();
    state = reduceEvent(state, {
      type: "tool_progress",
      tool_use_id: "child-bash",
      tool_name: "Bash",
      elapsed_time_seconds: 3.2,
    });
    const agent = state.turns[0].items.find((i) => i.kind === "tool");
    if (agent?.kind !== "tool") throw new Error("no agent");
    expect(agent.childTools[0].elapsedSeconds).toBe(3.2);
  });

  it("tool_result marks the child tool done with result", () => {
    let state = setupWithChild();
    state = reduceEvent(state, {
      type: "tool_progress",
      tool_use_id: "child-bash",
      tool_name: "Bash",
      elapsed_time_seconds: 1,
    });
    state = reduceEvent(state, {
      type: "tool_result",
      tool_use_id: "child-bash",
      content: "ok",
    });
    const agent = state.turns[0].items.find((i) => i.kind === "tool");
    if (agent?.kind !== "tool") throw new Error("no agent");
    expect(agent.childTools[0].status).toBe("done");
    expect(agent.childTools[0].result).toBe("ok");
  });

  it("tool_result updates a grandchild (multi-level recursive)", () => {
    let state = withOpenTurn("nested");
    state = reduceEvent(state, {
      type: "tool_call",
      tool_use_id: "agent-1",
      tool_name: "Agent",
      input: {},
    });
    state = reduceEvent(state, {
      type: "tool_call",
      tool_use_id: "child-agent",
      tool_name: "Agent",
      input: {},
      parent_tool_use_id: "agent-1",
    });
    state = reduceEvent(state, {
      type: "tool_call",
      tool_use_id: "grand-bash",
      tool_name: "Bash",
      input: {},
      parent_tool_use_id: "child-agent",
    });
    state = reduceEvent(state, {
      type: "tool_result",
      tool_use_id: "grand-bash",
      content: "done",
    });
    const agent = state.turns[0].items.find((i) => i.kind === "tool");
    if (agent?.kind !== "tool") throw new Error("no agent");
    expect(agent.childTools[0].childTools[0].status).toBe("done");
    expect(agent.childTools[0].childTools[0].result).toBe("done");
  });

  it("tool_progress for a non-existent id is a no-op on items (no crash)", () => {
    const before = setupWithChild();
    const state = reduceEvent(before, {
      type: "tool_progress",
      tool_use_id: "ghost",
      tool_name: "X",
      elapsed_time_seconds: 5,
    });
    expect(state.turns[0].items).toBe(before.turns[0].items);
  });
});
