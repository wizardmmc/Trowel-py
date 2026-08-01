import { describe, expect, it } from "vitest";

import type { AgentEvent } from "../agent/transport";
import {
  apiGetAgentHistory,
  apiGetCodexSubagentHistory,
  mockCreate,
} from "./ccStoreTestHarness";
import { createAgentStore } from "../agent";

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
    turn_id: `${threadId}-turn`,
    item_id: `${threadId}-item-${seq}`,
    payload,
  };
}

describe("createAgentStore - Codex child history reconciliation", () => {
  it("loads nested child histories discovered while replaying their parent", async () => {
    const store = createAgentStore();
    mockCreate("s1", {
      runtime: "codex",
      native_session_id: "parent-thread",
      model: "gpt-5.6-sol",
      capabilities: ["tools", "approval", "subagents"],
    });
    await store.getState().startSession({ workdir: "/wd", runtime: "codex" });
    apiGetAgentHistory.mockResolvedValueOnce([
      event(1, "user", "parent-thread", { text: "delegate" }),
      event(2, "subagent_activity", "parent-thread", {
        source: "subagent_activity",
        kind: "started",
        agent_thread_id: "child-thread",
        agent_path: "/root/child",
      }),
    ]);
    apiGetCodexSubagentHistory.mockImplementation(async (_sid, threadId) => {
      if (threadId === "child-thread") {
        return [
          event(1, "turn_start", "child-thread", { autonomous: true }),
          event(2, "subagent_activity", "child-thread", {
            source: "subagent_activity",
            kind: "started",
            agent_thread_id: "nested-thread",
            agent_path: "/root/child/nested",
          }),
          event(3, "finished", "child-thread"),
        ];
      }
      return [
        event(1, "turn_start", "nested-thread", { autonomous: true }),
        event(2, "text", "nested-thread", { text: "nested result" }),
        event(3, "finished", "nested-thread"),
      ];
    });

    await store.getState().loadHistoryIntoView();

    expect(apiGetCodexSubagentHistory).toHaveBeenCalledTimes(2);
    expect(apiGetCodexSubagentHistory).toHaveBeenCalledWith("s1", "nested-thread");
    expect(store.getState().sessions.s1.codexSubagents["nested-thread"]).toMatchObject({
      status: "completed",
      historyLoaded: true,
    });
  });
});
