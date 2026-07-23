import { describe, it, expect } from "vitest";
import {
  reduceEvent,
  INITIAL_REDUCER_STATE,
  withOpenTurn,
  installReducerTestReset,
} from "./support";

installReducerTestReset();

describe("reduceEvent — elicitation", () => {
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

describe("reduceEvent — turn_start", () => {
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
