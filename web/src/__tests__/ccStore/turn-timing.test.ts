import { describe, it, expect, afterEach, vi } from "vitest";
import {
  reduceEvent,
  INITIAL_REDUCER_STATE,
  endActiveTurnOnStreamClose,
  finalizeHistoryForView,
  withOpenTurn,
  type ReducerState,
  installReducerTestReset,
} from "./support";

installReducerTestReset();

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
    vi.setSystemTime(new Date("2026-07-16T12:00:00Z").getTime());
    const started = reduceEvent(INITIAL_REDUCER_STATE, {
      type: "user",
      text: "hi",
    });
    const withStart: ReducerState = {
      ...started,
      turns: [{ ...started.turns[0], startedAtMs: Date.now() }],
    };
    vi.setSystemTime(new Date("2026-07-16T12:01:18Z").getTime());
    const done = reduceEvent(withStart, {
      type: "finished",
      usage: {},
      total_cost_usd: 0,
      num_turns: 1,
    });
    expect(done.turns[0].status).toBe("done");
    expect(done.turns[0].durationSeconds).toBe(78);
    expect(done.turns[0].startedAtMs).toBeUndefined();
  });

  it("live: finished on a turn with no startedAtMs → no durationSeconds", () => {
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
    vi.setSystemTime(new Date("2026-07-16T12:00:00Z").getTime());
    const started = reduceEvent(INITIAL_REDUCER_STATE, {
      type: "user",
      text: "hi",
    });
    const withStart: ReducerState = {
      ...started,
      turns: [{ ...started.turns[0], startedAtMs: Date.now() }],
    };
    const done = reduceEvent(withStart, {
      type: "finished",
      usage: {},
      total_cost_usd: 0,
      num_turns: 1,
    });
    expect(done.turns[0].durationSeconds).toBeUndefined();
  });

  it("live: clock skew (finished before start) → no durationSeconds", () => {
    vi.setSystemTime(new Date("2026-07-16T12:00:10Z").getTime());
    const started = reduceEvent(INITIAL_REDUCER_STATE, {
      type: "user",
      text: "hi",
    });
    const withStart: ReducerState = {
      ...started,
      turns: [{ ...started.turns[0], startedAtMs: Date.now() }],
    };
    vi.setSystemTime(new Date("2026-07-16T12:00:05Z").getTime());
    const done = reduceEvent(withStart, {
      type: "finished",
      usage: {},
      total_cost_usd: 0,
      num_turns: 1,
    });
    expect(done.turns[0].durationSeconds).toBeUndefined();
  });
});

describe("endActiveTurnOnStreamClose — slash commands end the turn without a finished", () => {
  it("marks an active turn done on a clean stream close with no terminal event", () => {
    const before = withOpenTurn("/model glm-5.1");
    const after = endActiveTurnOnStreamClose(before, {
      aborted: false,
      transportOk: true,
    });
    expect(after.turns[0].status).toBe("done");
    expect(after.phase).toBe("done");
  });

  it("leaves the turn active on user abort (interrupt owns that transition)", () => {
    const before = withOpenTurn();
    const after = endActiveTurnOnStreamClose(before, {
      aborted: true,
      transportOk: true,
    });
    expect(after.turns[0].status).toBe("active");
    expect(after.phase).not.toBe("done");
  });

  it("leaves the turn active on transport failure (the error UI owns that)", () => {
    const before = withOpenTurn();
    const after = endActiveTurnOnStreamClose(before, {
      aborted: false,
      transportOk: false,
    });
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
    const after = endActiveTurnOnStreamClose(before, {
      aborted: false,
      transportOk: true,
    });
    expect(after.turns[0].status).toBe("done");
    expect(after.meta.costUsd).toBe(0.05);
  });

  it("preserves cost meta when closing an active /cost turn (no synthetic finished)", () => {
    let before = withOpenTurn("/cost");
    before = { ...before, meta: { ...before.meta, costUsd: 0.123 } };
    const after = endActiveTurnOnStreamClose(before, {
      aborted: false,
      transportOk: true,
    });
    expect(after.turns[0].status).toBe("done");
    expect(after.meta.costUsd).toBe(0.123);
  });
});

describe("finalizeHistoryForView — history is a completed past session", () => {
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
