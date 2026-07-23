import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import {
  reduceEvent,
  INITIAL_REDUCER_STATE,
  withOpenTurn,
  installReducerTestReset,
} from "./support";

installReducerTestReset();

describe("reduceEvent — thinking_progress heartbeats", () => {
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
    state = reduceEvent(state, {
      type: "thinking_progress",
      estimated_tokens: 26,
    });
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

describe("reduceEvent — thinking duration stamps thought-for-Ns", () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  it("stamps duration on the thinking item and clears startedAt", () => {
    vi.setSystemTime(10000);
    let state = withOpenTurn();
    state = reduceEvent(state, {
      type: "thinking_progress",
      estimated_tokens: 5,
    });
    vi.setSystemTime(22000);
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

  it("heartbeat-derived duration wins over a replay duration field", () => {
    vi.setSystemTime(10000);
    let state = withOpenTurn();
    state = reduceEvent(state, {
      type: "thinking_progress",
      estimated_tokens: 5,
    });
    vi.setSystemTime(22000);
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

describe("reduceEvent — thinking duration from history replay", () => {
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
