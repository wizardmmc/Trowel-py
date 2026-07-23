import { describe, it, expect } from "vitest";
import {
  reduceEvent,
  INITIAL_REDUCER_STATE,
  _resetTurnIdCounterForTests,
  run,
  type TrowelEvent,
  installReducerTestReset,
} from "./support";

installReducerTestReset();

describe("reduceEvent — Codex rate-limit", () => {
  it("INITIAL_REDUCER_STATE has null rateLimit", () => {
    expect(INITIAL_REDUCER_STATE.meta.rateLimit).toBeNull();
  });

  it("rate_limit_updated stores the snapshot on meta.rateLimit", () => {
    const state = run([
      {
        type: "rate_limit_updated",
        limit_id: "codex",
        limit_name: null,
        primary: {
          usedPercent: 84,
          windowDurationMins: 300,
          resetsAt: 1784949908,
        },
        secondary: null,
        credits: { hasCredits: false, unlimited: false, balance: "0" },
        individual_limit: null,
        spend_control_reached: null,
        plan_type: "pro",
        rate_limit_reached_type: null,
      },
    ]);
    expect(state.meta.rateLimit).toEqual({
      limit_id: "codex",
      limit_name: null,
      primary: {
        usedPercent: 84,
        windowDurationMins: 300,
        resetsAt: 1784949908,
      },
      secondary: null,
      credits: { hasCredits: false, unlimited: false, balance: "0" },
      individual_limit: null,
      spend_control_reached: null,
      plan_type: "pro",
      rate_limit_reached_type: null,
    });
  });

  it("preserves null sparse fields verbatim (real 2026-07-18 fixture shape)", () => {
    const state = run([
      {
        type: "rate_limit_updated",
        limit_id: "codex",
        limit_name: null,
        primary: {
          usedPercent: 20,
          windowDurationMins: 10080,
          resetsAt: 1784949908,
        },
        secondary: null,
        credits: { hasCredits: false, unlimited: false, balance: "0" },
        individual_limit: null,
        spend_control_reached: null,
        plan_type: "pro",
        rate_limit_reached_type: null,
      },
    ]);
    // 稀疏滚动更新中的 null 是协议事实，不能伪造成 0 或空对象。
    expect(state.meta.rateLimit?.secondary).toBeNull();
    expect(state.meta.rateLimit?.rate_limit_reached_type).toBeNull();
    expect(state.meta.rateLimit?.primary?.usedPercent).toBe(20);
  });

  it("a later rate_limit_updated replaces the prior snapshot (rolling update)", () => {
    const first = run([
      {
        type: "rate_limit_updated",
        limit_id: "codex",
        limit_name: null,
        primary: {
          usedPercent: 84,
          windowDurationMins: 300,
          resetsAt: 1784949908,
        },
        secondary: null,
        credits: null,
        individual_limit: null,
        spend_control_reached: null,
        plan_type: "pro",
        rate_limit_reached_type: null,
      },
    ]);
    const next = reduceEvent(first, {
      type: "rate_limit_updated",
      limit_id: "codex",
      limit_name: null,
      primary: {
        usedPercent: 12,
        windowDurationMins: 300,
        resetsAt: 1784953508,
      },
      secondary: null,
      credits: null,
      individual_limit: null,
      spend_control_reached: null,
      plan_type: "pro",
      rate_limit_reached_type: null,
    });
    expect(next.meta.rateLimit?.primary?.usedPercent).toBe(12);
    expect(next.meta.rateLimit?.primary?.resetsAt).toBe(1784953508);
  });

  it("stores rate_limit_reached_type when the limit is hit", () => {
    const state = run([
      {
        type: "rate_limit_updated",
        limit_id: "codex",
        limit_name: null,
        primary: {
          usedPercent: 100,
          windowDurationMins: 300,
          resetsAt: 1784949908,
        },
        secondary: null,
        credits: null,
        individual_limit: null,
        spend_control_reached: null,
        plan_type: "pro",
        rate_limit_reached_type: "rate_limit_reached",
      },
    ]);
    expect(state.meta.rateLimit?.rate_limit_reached_type).toBe(
      "rate_limit_reached",
    );
  });
});

describe("reduceEvent — Codex live/history deep-equal", () => {
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
      {
        type: "finished",
        usage: {},
        total_cost_usd: 0,
        num_turns: 1,
      } as TrowelEvent,
    ] as TrowelEvent[];

    const live = run(codexStream);
    // 生成的 turn id 也必须从相同起点比较，才能验证 reducer 状态深相等。
    _resetTurnIdCounterForTests();
    const history = run(codexStream);
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
