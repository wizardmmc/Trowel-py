import { describe, it, expect, beforeEach, vi, afterEach } from "vitest";
import {
  reduceEvent,
  INITIAL_REDUCER_STATE,
  _resetTurnIdCounterForTests,
  endActiveTurnOnStreamClose,
  finalizeHistoryForView,
  type ReducerState,
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

  it("slice-076: codex apply_patch tool_result carries write_diff onto ToolItem", () => {
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
          { oldStart: 1, oldLines: 1, newStart: 1, newLines: 1, lines: ["-hi", "+hey"] },
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

  it("slice-076: codex apply_patch declined tool_result keeps declined nativeStatus", () => {
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
    // slice-076 review M-1: _toolResultStatus must flip declined → ToolItem
    // status "failed" — that is the exact seam the green-check suppression
    // keys off. nativeStatus alone would still pass if this flip broke.
    expect(tool?.status).toBe("failed");
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

describe("reduceEvent — turn duration (用时)", () => {
  afterEach(() => vi.useRealTimers());

  it("history: stamps durationSeconds from the user event's duration_seconds", () => {
    const state = reduceEvent(INITIAL_REDUCER_STATE, {
      type: "user",
      text: "hi",
      duration_seconds: 78,
    });
    expect(state.turns[0].durationSeconds).toBe(78);
  });

  it("history: absent duration_seconds → no durationSeconds", () => {
    const state = reduceEvent(INITIAL_REDUCER_STATE, {
      type: "user",
      text: "hi",
    });
    expect(state.turns[0].durationSeconds).toBeUndefined();
  });

  it("live: finished computes durationSeconds from startedAtMs → now", () => {
    // startedAtMs is stamped by ccStore.send() on the optimistic turn; here it
    // is set directly to test the reducer's finished math in isolation.
    vi.setSystemTime(new Date("2026-07-16T12:00:00Z").getTime());
    const started = reduceEvent(INITIAL_REDUCER_STATE, {
      type: "user",
      text: "hi",
    });
    const withStart: ReducerState = {
      ...started,
      turns: [{ ...started.turns[0], startedAtMs: Date.now() }],
    };
    vi.setSystemTime(new Date("2026-07-16T12:01:18Z").getTime()); // +78s
    const done = reduceEvent(withStart, {
      type: "finished",
      usage: {},
      total_cost_usd: 0,
      num_turns: 1,
    });
    expect(done.turns[0].status).toBe("done");
    expect(done.turns[0].durationSeconds).toBe(78);
    expect(done.turns[0].startedAtMs).toBeUndefined(); // cleared after stamping
  });

  it("live: finished on a turn with no startedAtMs → no durationSeconds", () => {
    // A turn that never got send-time stamping must not get a fabricated value.
    const started = reduceEvent(INITIAL_REDUCER_STATE, {
      type: "user",
      text: "hi",
    });
    const done = reduceEvent(started, {
      type: "finished",
      usage: {},
      total_cost_usd: 0,
      num_turns: 1,
    });
    expect(done.turns[0].durationSeconds).toBeUndefined();
  });

  it("live: sub-second delta (rawDelta 0) → no durationSeconds, matches history", () => {
    // A turn that finishes within the same second it started. history would
    // drop a <=0 timestamp delta to None; live must match (no "Ran for 0s").
    vi.setSystemTime(new Date("2026-07-16T12:00:00Z").getTime());
    const started = reduceEvent(INITIAL_REDUCER_STATE, {
      type: "user",
      text: "hi",
    });
    const withStart: ReducerState = {
      ...started,
      turns: [{ ...started.turns[0], startedAtMs: Date.now() }],
    };
    // do NOT advance the clock → rawDelta 0 → fall back, no fabricated label
    const done = reduceEvent(withStart, {
      type: "finished",
      usage: {},
      total_cost_usd: 0,
      num_turns: 1,
    });
    expect(done.turns[0].durationSeconds).toBeUndefined();
  });

  it("live: clock skew (finished before start) → no durationSeconds", () => {
    // startedAtMs lies in the future relative to finished (NTP skew / sleep) →
    // negative rawDelta → drop, never a negative "Ran for -5s".
    vi.setSystemTime(new Date("2026-07-16T12:00:10Z").getTime());
    const started = reduceEvent(INITIAL_REDUCER_STATE, {
      type: "user",
      text: "hi",
    });
    const withStart: ReducerState = {
      ...started,
      turns: [{ ...started.turns[0], startedAtMs: Date.now() }],
    };
    vi.setSystemTime(new Date("2026-07-16T12:00:05Z").getTime()); // 5s BEFORE start
    const done = reduceEvent(withStart, {
      type: "finished",
      usage: {},
      total_cost_usd: 0,
      num_turns: 1,
    });
    expect(done.turns[0].durationSeconds).toBeUndefined();
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

  it("slice-077-prefix: task_started → task_notification → finished keeps one turn + done phase", () => {
    // 后端在中间 result 时缓冲不发 finished (C-3)，前端只在最终 result 收到
    // 一个 finished。回放确认前端不变量：subagent 事件流不提前结束 turn，
    // 全程一个 turn，最终 finished 才 phase=done。
    let state = withOpenTurn();
    state = reduceEvent(state, {
      type: "subagent_progress",
      tool_use_id: "call_bg",
      task_id: "b7cgk2tn3",
      status: "started",
      description: "sleep 6",
    });
    expect(state.phase).not.toBe("done");
    // 后台任务完成通知（后端 mid-turn 继续 drain，不发 finished）
    state = reduceEvent(state, {
      type: "subagent_progress",
      tool_use_id: "call_bg",
      task_id: "b7cgk2tn3",
      status: "completed",
      usage: { total_tokens: 0 },
    });
    expect(state.phase).not.toBe("done");
    expect(state.turns.length).toBe(1);
    // 最终 finished → 一个 turn、phase=done
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
    expect(state.turns[0].items.map((i) => i.kind)).toEqual(["tool", "subagent"]);
  });

  it("attaches to a NESTED Agent (subagent calling subagent), not a top-level standalone row", () => {
    // 嵌套 subagent：外层 Agent 调用内层 Agent。内层 Agent 的 tool_call 带
    // parent_tool_use_id → attach 进外层 childTools；内层的 subagent_progress
    // 事件 tool_use_id 指向内层 Agent。合并必须递归 childTools 找到内层，
    // 否则整条内层进度流匹配不到 → 溢出成顶层 standalone SubagentItem。
    // 实测一次嵌套调用撑出 313 个平铺 subagent 块（每个 last_tool_name 更新
    // 都新加一行），就是这条路径漏了递归。
    let state = withOpenTurn("use agent");
    state = reduceEvent(state, {
      type: "tool_call",
      tool_use_id: "outer",
      tool_name: "Agent",
      input: { description: "outer", subagent_type: "general-purpose" },
    });
    // 内层 Agent：parent 指向外层 → attach 进 outer.childTools，不进顶层
    state = reduceEvent(state, {
      type: "tool_call",
      tool_use_id: "inner",
      tool_name: "Agent",
      input: { description: "inner", subagent_type: "general-purpose" },
      parent_tool_use_id: "outer",
    });
    // 内层 Agent 的进度事件（started + progress）
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

    // 顶层只有一个 tool（outer）——没有溢出的 standalone subagent
    const top = state.turns[0].items;
    expect(top).toHaveLength(1);
    expect(top[0].kind).toBe("tool");
    if (top[0].kind === "tool") {
      // 进度合并进嵌套的内层 Agent，且多次 progress 合到同一个 inner（不新增）
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
    // 防御回归：mergeSubagentIntoTree 必须限定 toolName==="Agent"。一个嵌在
    // Agent.childTools 里的 Bash tool 即便 tool_use_id 撞了 subagent_progress
    // 的 id，也不能被合并——进度走 standalone fallback。
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
    // 顶层：agent tool + 一个溢出的 standalone subagent（没合并进 Bash）
    expect(top.map((i) => i.kind)).toEqual(["tool", "subagent"]);
    if (top[0].kind === "tool") {
      expect(top[0].childTools).toHaveLength(1);
      expect(top[0].childTools[0].toolName).toBe("Bash");
      // Bash child 没被塞 subagent
      expect(top[0].childTools[0].subagent).toBeUndefined();
    }
  });

  it("merges subagent_progress for a deeply nested Agent (3 levels)", () => {
    // 任意深度递归：outer → middle → inner。inner 的进度事件应合并到
    // inner.subagent（completed 也覆盖），顶层只有 outer，无溢出。
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

// ---------------------------------------------------------------------------
// slice-074: Codex events (unified to TrowelEvent names by the backend adapter)
// ---------------------------------------------------------------------------

describe("reduceEvent — slice-074 Codex mapping (post-adapter)", () => {
  it("Codex assistant_delta arrives as text and accumulates", () => {
    // The backend adapter renamed assistant_delta→text, payload.delta→payload.text.
    // After agentEventToTrowel unwraps, the reducer sees a flat text event.
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
    // text + thinking are distinct items (spec: reasoning & assistant separate)
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
      { type: "usage_updated", total: 25000, model_context_window: 200000 } as TrowelEvent,
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
      { type: "host_status", status: "host_exited", reason: "eof" } as TrowelEvent,
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
    expect(state.phase).toBe("idle"); // no running turn to error
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

describe("reduceEvent — slice-074 live/history deep-equal (Codex)", () => {
  /** The same recorded Codex event stream, fed once "live" (with an optimistic
   * user turn) and once as "history" (user event from the adapter), must reach
   * structurally equal turn/item state. seq is a transport concern (tracked in
   * the shell, not the reducer) so it is not part of the comparison. */
  it("replaying the same Codex events yields equal items/phase/meta-shape", () => {
    const codexStream = [
      { type: "user", text: "list files" },
      { type: "thinking", text: "planning" } as TrowelEvent,
      { type: "text", text: "running ls" } as TrowelEvent,
      {
        type: "tool_call",
        tool_use_id: "c1",
        tool_name: "command",
        input: {
          command: "ls",
          cwd: "/r",
          source: "unifiedExecStartup",
          command_actions: [{ type: "listFiles", command: "ls", path: null }],
        },
      } as TrowelEvent,
      {
        type: "tool_result",
        tool_use_id: "c1",
        content: "a.txt\nb.txt",
        exit_code: 0,
        duration_ms: 5,
      } as TrowelEvent,
      { type: "usage_updated", total: 100 } as TrowelEvent,
      { type: "finished", usage: {}, total_cost_usd: 0, num_turns: 1 } as TrowelEvent,
    ] as TrowelEvent[];

    const live = run(codexStream);
    _resetTurnIdCounterForTests(); // equal starting counter → equal generated turn ids
    const history = run(codexStream); // same reducer, same events → same state
    // Structural equality on the reducer-visible state (the spec contract:
    // live/history share one reducer → deep equal).
    expect(history.turns).toEqual(live.turns);
    expect(history.phase).toEqual(live.phase);
    expect(history.meta.usage).toEqual(live.meta.usage);
  });

  it("keeps Codex command_actions on the immutable ToolItem", () => {
    const state = run([
      { type: "user", text: "list files" },
      {
        type: "tool_call",
        tool_use_id: "c1",
        tool_name: "command",
        input: {
          command: "/bin/zsh -lc ls",
          cwd: "/r",
          source: "unifiedExecStartup",
          command_actions: [{ type: "listFiles", command: "ls", path: null }],
        },
      } as TrowelEvent,
    ]);
    const item = state.turns[0].items[0];
    expect(item).toMatchObject({
      kind: "tool",
      toolUseId: "c1",
      input: {
        source: "unifiedExecStartup",
        command_actions: [{ type: "listFiles", command: "ls", path: null }],
      },
    });
  });
});

describe("reduceEvent — slice-074 review fixes", () => {
  it("Codex live user-echo merges into the optimistic turn (no double turn)", () => {
    // send() creates an optimistic turn (active, empty, text "hi"). Codex then
    // emits a live user echo — it must NOT append a second turn (review codex HIGH).
    const optimistic = run([{ type: "user", text: "hi" }]);
    expect(optimistic.turns).toHaveLength(1);
    const afterEcho = reduceEvent(optimistic, { type: "user", text: "hi" });
    expect(afterEcho.turns).toHaveLength(1); // merged, not appended
  });

  it("history user event still appends when no optimistic turn exists", () => {
    // History replay starts fresh — each user event opens a new turn.
    const state = run([
      { type: "user", text: "first" },
      { type: "user", text: "second" },
    ]);
    expect(state.turns).toHaveLength(2);
    expect(state.turns[0].userText).toBe("first");
    expect(state.turns[1].userText).toBe("second");
  });

  it("turn_start keeps the optimistic turnId when the event omits one", () => {
    // Codex turn_start may carry null turn_id; the reducer must not clobber the
    // optimistic turn's id (review claude M-1).
    const withOptimistic = reduceEvent(
      { ...INITIAL_REDUCER_STATE, turns: [{ id: "turn-7", userText: "hi", items: [], status: "active", turnId: "native-9", revertible: false }] },
      { type: "turn_start", turn_id: undefined, revertible: false } as unknown as TrowelEvent,
    );
    expect(withOptimistic.turns[0].turnId).toBe("native-9"); // preserved, not nulled
  });
});

describe("reduceEvent — slice-074 Codex command failure (gpt5.6 Warning 3)", () => {
  it("tool_result with nativeStatus failed → ToolItem status failed", () => {
    const state = run([
      { type: "user", text: "go" },
      { type: "tool_call", tool_use_id: "c1", tool_name: "command", input: { command: "make" } } as TrowelEvent,
      { type: "tool_result", tool_use_id: "c1", content: "err", exit_code: 2, native_status: "failed" } as TrowelEvent,
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
      { type: "tool_call", tool_use_id: "c1", tool_name: "command", input: { command: "pwd" } } as TrowelEvent,
      { type: "tool_result", tool_use_id: "c1", content: "/r", exit_code: 0 } as TrowelEvent,
    ]);
    const tool = state.turns[0].items[0];
    if (tool.kind === "tool") expect(tool.status).toBe("done");
  });
});

describe("reduceEvent — Codex approval lifecycle (slice-075)", () => {
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
