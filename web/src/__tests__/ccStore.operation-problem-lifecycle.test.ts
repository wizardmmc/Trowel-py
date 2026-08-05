import { describe, expect, it } from "vitest";

import {
  apiAnswerElicit,
  apiRevertSession,
  ev,
  mockCreate,
  stream,
} from "./ccStoreTestHarness";
import { createAgentStore } from "../agent";

describe("createAgentStore — operation problem lifecycle", () => {
  it("clears an elicitation problem after the matching retry succeeds", async () => {
    const store = createAgentStore();
    mockCreate("s1");
    await store.getState().startSession({ workdir: "/wd" });
    apiAnswerElicit.mockRejectedValueOnce(new Error("answer failed"));

    await store.getState().answerElicit({ question: "yes" });

    expect(store.getState().sessions.s1.transportProblem?.operation).toBe(
      "elicitation_answer",
    );
    apiAnswerElicit.mockResolvedValueOnce({ ok: true });

    await store.getState().answerElicit({ question: "yes" });

    expect(store.getState().sessions.s1.transportError).toBeNull();
    expect(store.getState().sessions.s1.transportProblem).toBeNull();
  });

  it("clears a revert problem after the matching retry succeeds", async () => {
    const store = createAgentStore();
    mockCreate("s1");
    await store.getState().startSession({ workdir: "/wd" });
    await store.getState().send("first turn");
    stream.apply!(ev("turn_start", {}, { turn_id: "turn-1" }));
    stream.apply!(ev("finished", {}, { turn_id: "turn-1" }));
    apiRevertSession.mockRejectedValueOnce(new Error("revert failed"));

    await store.getState().revertTurn("turn-1");

    expect(store.getState().sessions.s1.transportProblem?.operation).toBe(
      "session_revert",
    );
    apiRevertSession.mockResolvedValueOnce({ reverted_turn_id: "turn-1" });

    await store.getState().revertTurn("turn-1");

    expect(store.getState().sessions.s1.transportError).toBeNull();
    expect(store.getState().sessions.s1.transportProblem).toBeNull();
  });

  it("keeps an async failure on the session that started the request", async () => {
    const store = createAgentStore();
    mockCreate("s1");
    await store.getState().startSession({ workdir: "/one" });
    await store.getState().send("keep first session");
    stream.apply!(ev("turn_start", {}, { session_id: "s1", turn_id: "turn-1" }));
    stream.apply!(ev("finished", {}, { session_id: "s1", turn_id: "turn-1" }));
    mockCreate("s2");
    await store.getState().startSession({ workdir: "/two" });
    store.setState({ activeSid: "s1" });
    let rejectAnswer!: (reason: Error) => void;
    apiAnswerElicit.mockImplementationOnce(
      () => new Promise((_resolve, reject) => { rejectAnswer = reject; }),
    );

    const answer = store.getState().answerElicit({ question: "yes" });
    store.setState({ activeSid: "s2" });
    rejectAnswer(new Error("answer failed"));
    await answer;

    expect(store.getState().sessions.s1.transportProblem?.operation).toBe(
      "elicitation_answer",
    );
    expect(store.getState().sessions.s2.transportProblem).toBeNull();
  });
});
