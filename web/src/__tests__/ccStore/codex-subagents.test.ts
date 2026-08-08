import { describe, expect, it } from "vitest";

import type { AgentEvent } from "../../agent/transport";
import { reduceAgentEvent } from "../../agent/application/store/eventState";
import { replayCodexSubagentHistory } from "../../agent/application/store/codexSubagents";
import {
  createReconciledSessionState,
  type PerSessionState,
} from "../../agent/application/store/sessionState";

function session(): PerSessionState {
  return createReconciledSessionState({
    session_id: "s1",
    runtime: "codex",
    native_session_id: "parent-thread-1",
    workdir: "/repo",
    model: "gpt-5.6-sol",
    effort: "high",
    permission: null,
    memory_enabled: true,
    profile_enabled: true,
    capabilities: ["tools", "approval", "subagents"],
    name: "repo",
    connected: true,
    running: true,
    turn_state: "running",
    current_turn_id: "parent-turn-1",
  });
}

function event(
  seq: number,
  type: string,
  threadId: string,
  payload: Record<string, unknown> = {},
): AgentEvent {
  return {
    schema: "agent-event-v1",
    session_id: "s1",
    runtime: "codex",
    seq,
    type,
    thread_id: threadId,
    turn_id: type === "subagent_activity" ? "parent-turn-1" : "child-turn-1",
    item_id: type === "subagent_activity" ? "activity-1" : "child-item-1",
    payload,
  };
}

function apply(current: PerSessionState, next: AgentEvent): PerSessionState {
  const result = reduceAgentEvent(current, next);
  expect(result.kind).toBe("updated");
  return result.kind === "updated" ? result.session : current;
}

describe("Codex child-thread routing", () => {
  it("reconciles a history-only started block to the child terminal state", () => {
    let current = apply(
      session(),
      event(1, "user", "parent-thread-1", { text: "delegate" }),
    );
    current = apply(
      current,
      event(2, "subagent_activity", "parent-thread-1", {
        source: "subagent_activity",
        kind: "started",
        agent_thread_id: "child-thread-1",
        agent_path: "/root/probe",
      }),
    );

    current = replayCodexSubagentHistory(current, "child-thread-1", [
      event(1, "turn_start", "child-thread-1", { autonomous: true }),
      event(2, "text", "child-thread-1", { text: "done" }),
      event(3, "finished", "child-thread-1"),
    ]);

    expect(current.codexSubagents["child-thread-1"].status).toBe("completed");
    expect(current.turns[0].items[0]).toMatchObject({
      kind: "subagent",
      subagent: { status: "completed" },
    });
  });

  it("reconciling one child preserves an unrelated child reducer identity", () => {
    let current = apply(
      session(),
      event(1, "user", "parent-thread-1", { text: "delegate" }),
    );
    for (const [seq, threadId, path] of [
      [2, "child-thread-1", "/root/one"],
      [3, "child-thread-2", "/root/two"],
    ] as const) {
      current = apply(
        current,
        event(seq, "subagent_activity", "parent-thread-1", {
          source: "subagent_activity",
          kind: "started",
          agent_thread_id: threadId,
          agent_path: path,
        }),
      );
    }
    const secondState = current.codexSubagents["child-thread-2"].state;

    current = replayCodexSubagentHistory(current, "child-thread-1", [
      event(1, "turn_start", "child-thread-1", { autonomous: true }),
      event(2, "finished", "child-thread-1"),
    ]);

    expect(current.codexSubagents["child-thread-2"].state).toBe(secondState);
  });

  it("keeps child output out of the parent timeline and preserves the parent turn", () => {
    let current = apply(
      session(),
      event(1, "user", "parent-thread-1", { text: "delegate" }),
    );
    current = apply(
      current,
      event(2, "subagent_activity", "parent-thread-1", {
        source: "subagent_activity",
        kind: "started",
        agent_thread_id: "child-thread-1",
        agent_path: "/root/probe",
      }),
    );
    current = apply(
      current,
      event(3, "turn_start", "child-thread-1", {
        autonomous: true,
        revertible: false,
      }),
    );
    current = apply(
      current,
      event(4, "text", "child-thread-1", { text: "child result" }),
    );
    current = apply(current, event(5, "finished", "child-thread-1"));

    expect(current.turns[0].userText).toBe("delegate");
    expect(current.turns[0].items).toHaveLength(1);
    expect(current.turns[0].status).toBe("active");
    expect(current.turnState).toBe("running");
    expect(current.currentTurnId).toBe("parent-turn-1");
    expect(current.codexSubagents["child-thread-1"].status).toBe("completed");
    expect(current.codexSubagents["child-thread-1"].state.turns[0].items).toEqual([
      { kind: "text", text: "child result" },
    ]);
  });

  it("records nested ownership from an activity emitted inside a child thread", () => {
    let current = session();
    for (const item of [
      event(1, "user", "parent-thread-1", { text: "delegate" }),
      event(2, "subagent_activity", "parent-thread-1", {
        source: "subagent_activity",
        kind: "started",
        agent_thread_id: "child-thread-1",
        agent_path: "/root/probe",
      }),
      event(3, "turn_start", "child-thread-1", { autonomous: true }),
      event(4, "subagent_activity", "child-thread-1", {
        source: "subagent_activity",
        kind: "started",
        agent_thread_id: "grandchild-thread-1",
        agent_path: "/root/probe/schema",
      }),
    ]) {
      current = apply(current, item);
    }

    expect(current.codexSubagents["grandchild-thread-1"].parentThreadId).toBe(
      "child-thread-1",
    );
  });

  it("updates repeated activity for one child without appending duplicate blocks", () => {
    let current = apply(
      session(),
      event(1, "user", "parent-thread-1", { text: "delegate" }),
    );
    for (const [seq, kind] of [
      [2, "started"],
      [3, "interacted"],
      [4, "interrupted"],
    ] as const) {
      current = apply(
        current,
        event(seq, "subagent_activity", "parent-thread-1", {
          source: "subagent_activity",
          kind,
          agent_thread_id: "child-thread-1",
          agent_path: "/root/probe",
        }),
      );
    }

    expect(current.turns[0].items).toHaveLength(1);
    expect(current.turns[0].items[0]).toMatchObject({
      kind: "subagent",
      subagent: { status: "cancelled" },
    });
  });

  it("marks an event from an unknown child thread for reconciliation", () => {
    const current = apply(
      session(),
      event(1, "finished", "unknown-child-thread"),
    );

    expect(current.needsReplay).toBe(true);
    expect(current.liveState).toBe("gapped");
    expect(current.turnState).toBe("unknown");
  });
});
