import { describe, expect, it } from "vitest";

import type { AgentSession } from "../../api/agent";
import type { AgentEvent } from "../../api/agentTypes";
import { replayAgentHistory } from "../../stores/ccStore/historyState";
import { createNewSessionState } from "../../stores/ccStore/sessionState";

const SESSION: AgentSession = {
  session_id: "s1",
  runtime: "claude_code",
  native_session_id: null,
  workdir: "/repo",
  model: "model",
  effort: null,
  permission: null,
  memory_enabled: true,
  profile_enabled: true,
  capabilities: ["tools"],
  name: "repo",
  connected: false,
  running: false,
};

function event(
  seq: number,
  type: string,
  payload: Record<string, unknown>,
): AgentEvent {
  return {
    schema: "agent-event-v1",
    session_id: "s1",
    runtime: "claude_code",
    seq,
    type,
    turn_id: null,
    item_id: null,
    payload,
  };
}

describe("replayAgentHistory", () => {
  it("按 history seq 去重并补齐只读终态", () => {
    const session = createNewSessionState(SESSION, { workdir: "/repo" });
    const replayed = replayAgentHistory(session, [
      event(1, "user", { text: "问题" }),
      event(2, "text", { text: "A" }),
      event(2, "text", { text: "重复" }),
    ]);

    expect(replayed.turns[0].items).toEqual([{ kind: "text", text: "A" }]);
    expect(replayed.turns[0].status).toBe("done");
    expect(replayed.phase).toBe("done");
  });

  it("回放后清空 live watermark 与缺口标记", () => {
    const session = {
      ...createNewSessionState(SESSION, { workdir: "/repo" }),
      lastSeq: 9,
      needsReplay: true,
    };
    const replayed = replayAgentHistory(session, [
      event(1, "user", { text: "问题" }),
    ]);

    expect(replayed.lastSeq).toBeNull();
    expect(replayed.needsReplay).toBe(false);
  });

  it("保留当前 thread Goal，但不从历史恢复旧 Plan", () => {
    const session = {
      ...createNewSessionState(
        { ...SESSION, runtime: "codex" },
        { workdir: "/repo", runtime: "codex" },
      ),
      goal: {
        objective: "Current Goal",
        status: "complete" as const,
        tokenBudget: 12000,
        tokensUsed: 8000,
        timeUsedSeconds: 20,
        createdAt: 1,
        updatedAt: 3,
      },
      plan: {
        explanation: null,
        steps: [{ step: "Stale step", status: "completed" as const }],
      },
    };
    const replayed = replayAgentHistory(session, [
      event(1, "goal_updated", {
        objective: "Historical Goal",
        status: "active",
        token_budget: 12000,
        tokens_used: 100,
        time_used_seconds: 1,
        created_at: 1,
        updated_at: 2,
      }),
      event(2, "plan_updated", {
        explanation: null,
        steps: [{ step: "Historical step", status: "pending" }],
      }),
    ]);

    expect(replayed.goal).toEqual(session.goal);
    expect(replayed.plan).toBeNull();
  });
});
