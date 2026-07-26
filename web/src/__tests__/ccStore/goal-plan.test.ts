import { describe, expect, it } from "vitest";

import {
  INITIAL_REDUCER_STATE,
  reduceEvent,
} from "../../stores/ccStore";

describe("Codex Goal and Plan reducer", () => {
  it("keeps the complete native Goal snapshot and clears it explicitly", () => {
    const updated = reduceEvent(INITIAL_REDUCER_STATE, {
      type: "goal_updated",
      objective: "Ship Goal and Plan",
      status: "active",
      token_budget: 12000,
      tokens_used: 7448,
      time_used_seconds: 9,
      created_at: 10,
      updated_at: 11,
    });

    expect(updated.goal).toEqual({
      objective: "Ship Goal and Plan",
      status: "active",
      tokenBudget: 12000,
      tokensUsed: 7448,
      timeUsedSeconds: 9,
      createdAt: 10,
      updatedAt: 11,
    });
    expect(reduceEvent(updated, { type: "goal_cleared" }).goal).toBeNull();
  });

  it("replaces the Plan snapshot and clears it when a new turn starts", () => {
    const first = reduceEvent(INITIAL_REDUCER_STATE, {
      type: "plan_updated",
      explanation: "first plan",
      steps: [{ step: "Inspect", status: "inProgress" }],
    });
    const replaced = reduceEvent(first, {
      type: "plan_updated",
      explanation: null,
      steps: [
        { step: "Inspect", status: "completed" },
        { step: "Implement", status: "pending" },
      ],
    });

    expect(replaced.plan?.steps).toHaveLength(2);
    expect(replaced.plan?.steps[0].status).toBe("completed");
    expect(
      reduceEvent(replaced, {
        type: "turn_start",
        turn_id: "turn-2",
        revertible: false,
        autonomous: true,
      }).plan,
    ).toBeNull();
  });

  it("creates a content turn for an autonomous Goal continuation", () => {
    const state = reduceEvent(INITIAL_REDUCER_STATE, {
      type: "turn_start",
      turn_id: "turn-auto",
      revertible: false,
      autonomous: true,
    });

    expect(state.turns).toHaveLength(1);
    expect(state.turns[0]).toMatchObject({
      userText: "",
      turnId: "turn-auto",
      status: "active",
    });
    expect(state.phase).toBe("awaiting_first");
  });
});
