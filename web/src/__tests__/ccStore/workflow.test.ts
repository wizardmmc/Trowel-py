import { describe, it, expect } from "vitest";
import {
  reduceEvent,
  run,
  type TrowelEvent,
  installReducerTestReset,
} from "./support";

installReducerTestReset();

describe("reduceEvent — workflow_tree", () => {
  function wfEvent(
    overrides: Partial<Record<string, unknown>> = {},
  ): TrowelEvent {
    return {
      type: "workflow_tree",
      run_id: "wf_1",
      task_id: "task_1",
      name: "baseline",
      args: "test question",
      status: "completed",
      agent_count: 3,
      done_count: 3,
      total_tokens: 1000,
      total_tool_calls: 5,
      duration_ms: 12345,
      phases: [{ title: "Scope", detail: "decompose" }],
      agents: [
        {
          agent_id: "a1",
          label: "scope",
          phase_index: 1,
          phase_title: "Scope",
          model: "glm-5.1",
          state: "done",
          tokens: 100,
          tool_calls: 1,
          last_tool_name: "Bash",
          duration_ms: 1000,
          prompt_preview: "p",
          result_preview: "r",
        },
      ],
      error: null,
      ...overrides,
    } as TrowelEvent;
  }

  it("appends a workflow item to the current turn", () => {
    const state = run([{ type: "user", text: "go" }, wfEvent()]);
    const last = state.turns[state.turns.length - 1];
    const wfs = last.items.filter((i) => i.kind === "workflow");
    expect(wfs).toHaveLength(1);
  });

  it("replaces the prior snapshot matched by run_id (not append)", () => {
    const state = run([
      { type: "user", text: "go" },
      wfEvent({ status: "running", done_count: 1 }),
      wfEvent({ status: "completed", done_count: 3 }),
    ]);
    const last = state.turns[state.turns.length - 1];
    const wfs = last.items.filter((i) => i.kind === "workflow");
    expect(wfs).toHaveLength(1);
    expect((wfs[0] as { status: string }).status).toBe("completed");
  });

  it("updates the workflow in its LAUNCH turn even from a later turn", () => {
    let state = run([
      { type: "user", text: "launch" },
      wfEvent({ status: "running" }),
    ]);
    state = reduceEvent(state, { type: "user", text: "status?" });
    state = reduceEvent(state, wfEvent({ status: "completed" }));
    const turn1 = state.turns[0];
    const turn2 = state.turns[1];
    const t1Wf = turn1.items.find((i) => i.kind === "workflow");
    const t2Wf = turn2.items.find((i) => i.kind === "workflow");
    expect(t1Wf).toBeDefined();
    expect((t1Wf as { status: string }).status).toBe("completed");
    expect(t2Wf).toBeUndefined();
  });

  it("tracks distinct run_ids independently", () => {
    const state = run([
      { type: "user", text: "go" },
      wfEvent({ run_id: "wf_a", status: "running" }),
      wfEvent({ run_id: "wf_b", status: "completed" }),
      wfEvent({ run_id: "wf_a", status: "completed" }),
    ]);
    const last = state.turns[state.turns.length - 1];
    const wfs = last.items.filter((i) => i.kind === "workflow");
    expect(wfs).toHaveLength(2);
    const byId = new Map(wfs.map((w) => [(w as { runId: string }).runId, w]));
    expect((byId.get("wf_a") as { status: string }).status).toBe("completed");
    expect((byId.get("wf_b") as { status: string }).status).toBe("completed");
  });

  it("drops subagent_progress when a workflow item exists", () => {
    const state = run([
      { type: "user", text: "go" },
      wfEvent({ status: "running" }),
      {
        type: "subagent_progress",
        tool_use_id: "tu_unknown",
        task_id: "task_wf_agent",
        status: "progress",
      } as TrowelEvent,
    ]);
    const last = state.turns[state.turns.length - 1];
    expect(last.items.some((i) => i.kind === "subagent")).toBe(false);
  });

  it("keeps standalone subagent when NO workflow exists", () => {
    const state = run([
      { type: "user", text: "go" },
      {
        type: "subagent_progress",
        tool_use_id: "tu_unknown",
        task_id: "task_x",
        status: "progress",
      } as TrowelEvent,
    ]);
    const last = state.turns[state.turns.length - 1];
    expect(last.items.some((i) => i.kind === "subagent")).toBe(true);
  });
});
