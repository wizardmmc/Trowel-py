import { describe, it, expect, beforeEach, vi, afterEach } from "vitest";
import {
  reduceEvent,
  INITIAL_REDUCER_STATE,
  _resetTurnIdCounterForTests,
  endActiveTurnOnStreamClose,
  finalizeHistoryForView,
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

describe("reduceEvent — model_changed (slice-027)", () => {
  it("updates meta.model from event.model", () => {
    const started = run([
      { type: "session_started", model: "sonnet", cwd: "/wd",
        cc_session_id: "s1", tools: [] },
    ]);
    const next = reduceEvent(started, {
      type: "model_changed", model: "opus", effort: null,
    });
    expect(next.meta.model).toBe("opus");
  });

  it("keeps previous model when event.model is null (follow settings)", () => {
    const started = run([
      { type: "session_started", model: "sonnet", cwd: "/wd",
        cc_session_id: "s1", tools: [] },
    ]);
    const next = reduceEvent(started, {
      type: "model_changed", model: null, effort: "high",
    });
    expect(next.meta.model).toBe("sonnet"); // unchanged
  });

  it("is a no-op (same ref) when model unchanged", () => {
    const started = run([
      { type: "session_started", model: "opus", cwd: "/wd",
        cc_session_id: "s1", tools: [] },
    ]);
    const next = reduceEvent(started, {
      type: "model_changed", model: "opus", effort: null,
    });
    expect(next).toBe(started); // same reference → no rerender
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

  it("slice-033 feat 2: tool_result carries write_diff -> merged onto ToolItem", () => {
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

  it("slice-033 feat 2: tool_result WITHOUT write_diff leaves writeDiff undefined", () => {
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

  it("stalled_warning stores a heads-up on meta without changing phase", () => {
    const state = reduceEvent(withOpenTurn(), {
      type: "stalled_warning",
      severity: "mild",
      elapsed_s: 120,
    });
    expect(state.meta.stallWarning).toEqual({ severity: "mild", elapsed_s: 120 });
    // phase is untouched (cc still running, just silent) + no item added
    expect(state.phase).not.toBe("stalled");
    expect(state.turns[0].items).toHaveLength(0);
  });

  it("stalled_warning is cleared by any subsequent event", () => {
    const warned = reduceEvent(withOpenTurn(), {
      type: "stalled_warning",
      severity: "severe",
      elapsed_s: 300,
    });
    const cleared = reduceEvent(warned, { type: "text", text: "cc is back" });
    expect(cleared.meta.stallWarning).toBeNull();
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

describe("endActiveTurnOnStreamClose — slash commands end the turn without a finished", () => {
  // The host's /model, /effort, /cost, /status and unsupported-slash paths
  // emit one status/local_command event then close the stream — no finished.
  // Without closing the turn, the composer stays stuck in "生成中" forever.

  it("marks an active turn done on a clean stream close with no terminal event", () => {
    const before = withOpenTurn("/model glm-5.1");
    const after = endActiveTurnOnStreamClose(before, { aborted: false, transportOk: true });
    expect(after.turns[0].status).toBe("done");
    expect(after.phase).toBe("done");
  });

  it("leaves the turn active on user abort (interrupt owns that transition)", () => {
    const before = withOpenTurn();
    const after = endActiveTurnOnStreamClose(before, { aborted: true, transportOk: true });
    expect(after.turns[0].status).toBe("active");
    expect(after.phase).not.toBe("done");
  });

  it("leaves the turn active on transport failure (the error UI owns that)", () => {
    const before = withOpenTurn();
    const after = endActiveTurnOnStreamClose(before, { aborted: false, transportOk: false });
    expect(after.turns[0].status).toBe("active");
  });

  it("does not double-finish a turn already ended by finished, and keeps cost meta", () => {
    let before = withOpenTurn();
    before = reduceEvent(before, {
      type: "finished",
      usage: {},
      total_cost_usd: 0.05,
      num_turns: 1,
    });
    const after = endActiveTurnOnStreamClose(before, { aborted: false, transportOk: true });
    expect(after.turns[0].status).toBe("done");
    expect(after.meta.costUsd).toBe(0.05);
  });

  it("preserves cost meta when closing an active /cost turn (no synthetic finished)", () => {
    let before = withOpenTurn("/cost");
    before = { ...before, meta: { ...before.meta, costUsd: 0.123 } };
    const after = endActiveTurnOnStreamClose(before, { aborted: false, transportOk: true });
    expect(after.turns[0].status).toBe("done");
    expect(after.meta.costUsd).toBe(0.123);
  });
});

describe("finalizeHistoryForView — history is a completed past session", () => {
  // CC's jsonl has no `result` line, so history replay never sees a finished
  // event. Without finalizing, every past turn stays "active" and phase stays
  // "generating" — which disables the composer (can't continue a loaded
  // session, blocks slice024 E3).

  it("flips active turns to done and an in-progress phase to done", () => {
    let state = withOpenTurn("你好今天几号");
    state = reduceEvent(state, { type: "text", text: "今天是星期四" });
    expect(state.phase).toBe("generating");
    expect(state.turns[0].status).toBe("active");
    const after = finalizeHistoryForView(state);
    expect(after.phase).toBe("done");
    expect(after.turns.every((t) => t.status === "done")).toBe(true);
  });

  it("preserves turn content when finalizing", () => {
    let state = withOpenTurn("q");
    state = reduceEvent(state, { type: "text", text: "a" });
    const after = finalizeHistoryForView(state);
    expect(after.turns[0].userText).toBe("q");
    expect(after.turns[0].items.some((i) => i.kind === "text")).toBe(true);
  });

  it("leaves already-terminal turns (error) as-is", () => {
    let state = withOpenTurn();
    state = reduceEvent(state, {
      type: "error",
      subclass: "error_max_turns",
      errors: ["loop"],
      api_error_status: null,
    });
    const after = finalizeHistoryForView(state);
    expect(after.turns[0].status).toBe("error");
    expect(after.phase).toBe("error");
  });
});

describe("reduceEvent — thinking_progress heartbeats (slice-025-a A1)", () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  it("first heartbeat records thinkingStartedAt + tokens + sets phase thinking", () => {
    vi.setSystemTime(10000);
    const state = reduceEvent(INITIAL_REDUCER_STATE, {
      type: "thinking_progress",
      estimated_tokens: 5,
    });
    expect(state.phase).toBe("thinking");
    expect(state.meta.thinkingStartedAt).toBe(10000);
    expect(state.meta.thinkingTokens).toBe(5);
  });

  it("later heartbeats refresh tokens but keep the first startedAt", () => {
    vi.setSystemTime(10000);
    let state = reduceEvent(INITIAL_REDUCER_STATE, {
      type: "thinking_progress",
      estimated_tokens: 5,
    });
    vi.setSystemTime(15000);
    state = reduceEvent(state, { type: "thinking_progress", estimated_tokens: 26 });
    expect(state.meta.thinkingStartedAt).toBe(10000);
    expect(state.meta.thinkingTokens).toBe(26);
  });

  it("does not append any item (preserves item order for B1)", () => {
    const state = reduceEvent(INITIAL_REDUCER_STATE, {
      type: "thinking_progress",
      estimated_tokens: 5,
    });
    expect(state.turns).toHaveLength(0);
  });
});

describe("reduceEvent — thinking duration stamps thought-for-Ns (slice-025-a A2)", () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  it("stamps duration on the thinking item and clears startedAt", () => {
    vi.setSystemTime(10000);
    let state = withOpenTurn();
    state = reduceEvent(state, { type: "thinking_progress", estimated_tokens: 5 });
    vi.setSystemTime(22000); // 12s later
    state = reduceEvent(state, { type: "thinking", text: "reasoning..." });
    const item = state.turns[0].items[0];
    expect(item.kind).toBe("thinking");
    if (item.kind === "thinking") {
      expect(item.thinkingDurationSeconds).toBe(12);
    }
    expect(state.meta.thinkingStartedAt).toBeNull();
    expect(state.meta.thinkingTokens).toBeNull();
  });

  it("without a prior heartbeat, leaves duration undefined", () => {
    const state = reduceEvent(withOpenTurn(), { type: "thinking", text: "x" });
    const item = state.turns[0].items[0];
    if (item.kind === "thinking") {
      expect(item.thinkingDurationSeconds).toBeUndefined();
    }
  });

  it("heartbeat-derived duration wins over a replay duration field (slice-031)", () => {
    vi.setSystemTime(10000);
    let state = withOpenTurn();
    state = reduceEvent(state, { type: "thinking_progress", estimated_tokens: 5 });
    vi.setSystemTime(22000); // 12s later
    // A live event would not carry thinking_duration_seconds, but if it did,
    // the heartbeat measurement must still take priority.
    state = reduceEvent(state, {
      type: "thinking",
      text: "x",
      thinking_duration_seconds: 99,
    });
    const item = state.turns[0].items[0];
    if (item.kind === "thinking") {
      expect(item.thinkingDurationSeconds).toBe(12);
    }
  });
});

describe("reduceEvent — thinking duration from history replay (slice-031)", () => {
  // No fake timers needed: the replay path has no heartbeat, so Date.now() is
  // never read. Duration comes straight from event.thinking_duration_seconds.

  it("stamps duration from the replay event when no heartbeat preceded", () => {
    const state = reduceEvent(withOpenTurn(), {
      type: "thinking",
      text: "reasoning...",
      thinking_duration_seconds: 23,
    });
    const item = state.turns[0].items[0];
    expect(item.kind).toBe("thinking");
    if (item.kind === "thinking") {
      expect(item.thinkingDurationSeconds).toBe(23);
    }
  });

  it("a replay event without the duration field leaves it undefined", () => {
    const state = reduceEvent(withOpenTurn(), { type: "thinking", text: "x" });
    const item = state.turns[0].items[0];
    if (item.kind === "thinking") {
      expect(item.thinkingDurationSeconds).toBeUndefined();
    }
  });
});

describe("reduceEvent — subagent_progress (slice-025-a A3)", () => {
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
    expect(state.turns[0].items.map((i) => i.kind)).toEqual(["tool", "subagent"]);
  });
});

describe("reduceEvent — sub-agent childTools attach (slice-025-a 阶段B)", () => {
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

describe("reduceEvent — childTools progress/result recursive update (阶段B)", () => {
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
    state = reduceEvent(state, { type: "tool_call", tool_use_id: "agent-1", tool_name: "Agent", input: {} });
    state = reduceEvent(state, { type: "tool_call", tool_use_id: "child-agent", tool_name: "Agent", input: {}, parent_tool_use_id: "agent-1" });
    state = reduceEvent(state, { type: "tool_call", tool_use_id: "grand-bash", tool_name: "Bash", input: {}, parent_tool_use_id: "child-agent" });
    state = reduceEvent(state, { type: "tool_result", tool_use_id: "grand-bash", content: "done" });
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

describe("reduceEvent — elicitation (slice-025-c)", () => {
  it("elicit_request appends a pending ElicitationItem and sets phase awaiting_input", () => {
    const state = reduceEvent(withOpenTurn(), {
      type: "elicit_request",
      tool_use_id: "call_1",
      request_id: "req-1",
      questions: [
        {
          question: "A or B?",
          header: "Pref",
          options: [{ label: "A" }, { label: "B" }],
          multiSelect: false,
        },
      ],
    });
    expect(state.phase).toBe("awaiting_input");
    const last = state.turns[state.turns.length - 1];
    const elicit = last.items.find((it) => it.kind === "elicit");
    expect(elicit).toBeDefined();
    if (elicit?.kind !== "elicit") throw new Error("expected elicit item");
    expect(elicit.status).toBe("pending");
    expect(elicit.toolUseId).toBe("call_1");
    expect(elicit.requestId).toBe("req-1");
    expect(elicit.questions[0].header).toBe("Pref");
    expect(elicit.resultText).toBeNull();
  });

  it("tool_result flips the matching pending elicit to answered", () => {
    let state = withOpenTurn();
    state = reduceEvent(state, {
      type: "elicit_request",
      tool_use_id: "call_1",
      request_id: "r",
      questions: [
        {
          question: "A or B?",
          header: "Pref",
          options: [{ label: "A" }],
          multiSelect: false,
        },
      ],
    });
    state = reduceEvent(state, {
      type: "tool_result",
      tool_use_id: "call_1",
      content: "User has answered: A or B?=A",
    });
    const last = state.turns[state.turns.length - 1];
    const elicit = last.items.find((it) => it.kind === "elicit");
    if (elicit?.kind !== "elicit") throw new Error("expected elicit item");
    expect(elicit.status).toBe("answered");
    expect(elicit.resultText).toBe("User has answered: A or B?=A");
  });

  it("tool_result with unmatched id still routes to the ordinary tool path", () => {
    let state = withOpenTurn();
    state = reduceEvent(state, {
      type: "tool_call",
      tool_use_id: "tu_other",
      tool_name: "Bash",
      input: { command: "ls" },
    });
    state = reduceEvent(state, {
      type: "tool_result",
      tool_use_id: "tu_other",
      content: "file.txt",
    });
    const last = state.turns[state.turns.length - 1];
    expect(last.items.some((it) => it.kind === "elicit")).toBe(false);
    const tool = last.items.find((it) => it.kind === "tool");
    if (tool?.kind !== "tool") throw new Error("expected tool item");
    expect(tool.status).toBe("done");
    expect(tool.result).toBe("file.txt");
  });
});

describe("reduceEvent — turn_start (slice-026)", () => {
  it("attaches turnId + revertible to the current turn", () => {
    const state = reduceEvent(withOpenTurn("hi"), {
      type: "turn_start",
      turn_id: "tid-1",
      revertible: true,
    });
    const last = state.turns[state.turns.length - 1];
    expect(last.turnId).toBe("tid-1");
    expect(last.revertible).toBe(true);
  });

  it("no-op when there is no current turn", () => {
    const state = reduceEvent(INITIAL_REDUCER_STATE, {
      type: "turn_start",
      turn_id: "x",
      revertible: true,
    });
    expect(state.turns).toHaveLength(0);
  });

  it("an optimistic user turn defaults to non-revertible until turn_start arrives", () => {
    const t = withOpenTurn("hi").turns[0];
    expect(t.turnId).toBeNull();
    expect(t.revertible).toBe(false);
  });
});

// ============================================================
// slice-028: tasks (V2 TaskCreate/TaskUpdate) + session_exited
// ============================================================

describe("reduceEvent — tasks (slice-028 V2)", () => {
  it("TaskCreate tool_call appends a pending task (taskId null until result)", () => {
    let state = withOpenTurn("do stuff");
    state = reduceEvent(state, {
      type: "tool_call",
      tool_use_id: "tu_1",
      tool_name: "TaskCreate",
      input: {
        subject: "后端多开 API",
        description: "registry + workdir 索引",
        activeForm: "写后端多开",
      },
    });
    expect(state.tasks).toHaveLength(1);
    const task = state.tasks[0];
    expect(task).toMatchObject({
      subject: "后端多开 API",
      description: "registry + workdir 索引",
      activeForm: "写后端多开",
      status: "pending",
      toolUseId: "tu_1",
      taskId: null,
    });
  });

  it("TaskCreate tool_call still appends a ToolItem (message stream keeps it)", () => {
    let state = withOpenTurn();
    state = reduceEvent(state, {
      type: "tool_call",
      tool_use_id: "tu_1",
      tool_name: "TaskCreate",
      input: { subject: "x" },
    });
    const last = state.turns[state.turns.length - 1];
    expect(last.items.some((i) => i.kind === "tool" && i.toolName === "TaskCreate"))
      .toBe(true);
  });

  it("TaskCreate tool_result assigns taskId parsed from 'Task #N created'", () => {
    let state = withOpenTurn();
    state = reduceEvent(state, {
      type: "tool_call",
      tool_use_id: "tu_1",
      tool_name: "TaskCreate",
      input: { subject: "研究" },
    });
    state = reduceEvent(state, {
      type: "tool_result",
      tool_use_id: "tu_1",
      content: "Task #3 created successfully: 研究",
    });
    expect(state.tasks[0].taskId).toBe("3");
  });

  it("TaskUpdate tool_call flips the matching task's status", () => {
    let state = withOpenTurn();
    state = reduceEvent(state, {
      type: "tool_call",
      tool_use_id: "tu_1",
      tool_name: "TaskCreate",
      input: { subject: "a" },
    });
    state = reduceEvent(state, {
      type: "tool_result",
      tool_use_id: "tu_1",
      content: "Task #1 created successfully: a",
    });
    state = reduceEvent(state, {
      type: "tool_call",
      tool_use_id: "tu_2",
      tool_name: "TaskUpdate",
      input: { taskId: "1", status: "in_progress" },
    });
    expect(state.tasks[0].status).toBe("in_progress");
  });

  it("TaskUpdate completed transitions stick", () => {
    let state = withOpenTurn();
    state = reduceEvent(state, {
      type: "tool_call", tool_use_id: "tu_1", tool_name: "TaskCreate",
      input: { subject: "a" },
    });
    state = reduceEvent(state, {
      type: "tool_result", tool_use_id: "tu_1",
      content: "Task #1 created successfully: a",
    });
    state = reduceEvent(state, {
      type: "tool_call", tool_use_id: "tu_2", tool_name: "TaskUpdate",
      input: { taskId: "1", status: "completed" },
    });
    expect(state.tasks[0].status).toBe("completed");
  });

  it("multiple tasks: each TaskCreate result assigns its own taskId", () => {
    let state = withOpenTurn();
    state = reduceEvent(state, {
      type: "tool_call", tool_use_id: "tu_1", tool_name: "TaskCreate",
      input: { subject: "a" },
    });
    state = reduceEvent(state, {
      type: "tool_call", tool_use_id: "tu_2", tool_name: "TaskCreate",
      input: { subject: "b" },
    });
    state = reduceEvent(state, {
      type: "tool_result", tool_use_id: "tu_1",
      content: "Task #1 created successfully: a",
    });
    state = reduceEvent(state, {
      type: "tool_result", tool_use_id: "tu_2",
      content: "Task #2 created successfully: b",
    });
    expect(state.tasks.map((t) => t.taskId)).toEqual(["1", "2"]);
    expect(state.tasks.map((t) => t.subject)).toEqual(["a", "b"]);
  });

  it("TaskUpdate for an unknown taskId is a no-op (no crash)", () => {
    let state = withOpenTurn();
    state = reduceEvent(state, {
      type: "tool_call", tool_use_id: "tu_1", tool_name: "TaskCreate",
      input: { subject: "a" },
    });
    const before = state.tasks;
    state = reduceEvent(state, {
      type: "tool_call", tool_use_id: "tu_2", tool_name: "TaskUpdate",
      input: { taskId: "999", status: "in_progress" },
    });
    expect(state.tasks).toBe(before); // same ref — no change
  });

  it("tasks persist across turns (session-scoped, not reset on new user turn)", () => {
    let state = withOpenTurn("q1");
    state = reduceEvent(state, {
      type: "tool_call", tool_use_id: "tu_1", tool_name: "TaskCreate",
      input: { subject: "a" },
    });
    // start a second turn (history-style user event)
    state = reduceEvent(state, { type: "user", text: "q2" });
    expect(state.tasks).toHaveLength(1);
    expect(state.tasks[0].subject).toBe("a");
  });

  it("a non-task tool_result does not disturb tasks", () => {
    let state = withOpenTurn();
    state = reduceEvent(state, {
      type: "tool_call", tool_use_id: "tu_1", tool_name: "TaskCreate",
      input: { subject: "a" },
    });
    state = reduceEvent(state, {
      type: "tool_call", tool_use_id: "bash_1", tool_name: "Bash",
      input: { command: "ls" },
    });
    state = reduceEvent(state, {
      type: "tool_result", tool_use_id: "bash_1", content: "file.txt",
    });
    expect(state.tasks).toHaveLength(1);
    expect(state.tasks[0].taskId).toBeNull();
  });
});

describe("reduceEvent — session_exited (slice-028 bug3)", () => {
  it("marks meta.exited + returncode", () => {
    const state = reduceEvent(INITIAL_REDUCER_STATE, {
      type: "session_exited",
      returncode: 0,
    });
    expect(state.meta.exited).toBe(true);
    expect(state.meta.exitReturncode).toBe(0);
  });

  it("does not drop turns or tasks (exited is a session-lifecycle flag)", () => {
    let state = withOpenTurn("hi");
    state = reduceEvent(state, {
      type: "tool_call", tool_use_id: "tu_1", tool_name: "TaskCreate",
      input: { subject: "a" },
    });
    const before = state.turns.length;
    state = reduceEvent(state, { type: "session_exited", returncode: 0 });
    expect(state.turns).toHaveLength(before);
    expect(state.tasks).toHaveLength(1);
    expect(state.meta.exited).toBe(true);
  });
});

// ── slice-036: workflow_tree reducer ───────────────────────────────────────

describe("reduceEvent — workflow_tree (slice-036)", () => {
  /** A minimal completed workflow snapshot (3 agents, 2 phases). */
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
    expect(wfs).toHaveLength(1); // replaced, not duplicated
    expect((wfs[0] as { status: string }).status).toBe("completed");
  });

  it("updates the workflow in its LAUNCH turn even from a later turn", () => {
    // turn 1 launches the workflow (running snapshot lands there)
    let state = run([{ type: "user", text: "launch" }, wfEvent({ status: "running" })]);
    // turn 2: user sends another message — a later snapshot arrives
    state = reduceEvent(state, { type: "user", text: "status?" });
    state = reduceEvent(state, wfEvent({ status: "completed" }));
    const turn1 = state.turns[0];
    const turn2 = state.turns[1];
    const t1Wf = turn1.items.find((i) => i.kind === "workflow");
    const t2Wf = turn2.items.find((i) => i.kind === "workflow");
    expect(t1Wf).toBeDefined(); // updated in place
    expect((t1Wf as { status: string }).status).toBe("completed");
    expect(t2Wf).toBeUndefined(); // NOT duplicated into turn 2
  });

  it("tracks distinct run_ids independently (C-6 multi-workflow)", () => {
    const state = run([
      { type: "user", text: "go" },
      wfEvent({ run_id: "wf_a", status: "running" }),
      wfEvent({ run_id: "wf_b", status: "completed" }),
      wfEvent({ run_id: "wf_a", status: "completed" }),
    ]);
    const last = state.turns[state.turns.length - 1];
    const wfs = last.items.filter((i) => i.kind === "workflow");
    expect(wfs).toHaveLength(2);
    const byId = new Map(
      wfs.map((w) => [(w as { runId: string }).runId, w]),
    );
    expect((byId.get("wf_a") as { status: string }).status).toBe("completed");
    expect((byId.get("wf_b") as { status: string }).status).toBe("completed");
  });

  it("drops subagent_progress when a workflow item exists (slice-036 bug2)", () => {
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

  it("keeps standalone subagent when NO workflow exists (decision #10)", () => {
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
