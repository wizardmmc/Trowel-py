import { describe, expect, it } from "vitest";

import type {
  AgentPendingRequest,
  AgentSession,
} from "../../api/agent";
import { applyPendingApproval } from "../../stores/ccStore/approvalState";
import { createNewSessionState } from "../../stores/ccStore/sessionState";

const SESSION: AgentSession = {
  session_id: "s1",
  runtime: "codex",
  native_session_id: "thread-1",
  workdir: "/repo",
  model: "model",
  effort: null,
  permission: null,
  memory_enabled: true,
  profile_enabled: true,
  capabilities: ["approval"],
  name: "repo",
  connected: true,
  running: false,
};

function request(turnId: string): AgentPendingRequest {
  return {
    request_id: `request-${turnId}`,
    session_id: "s1",
    thread_id: "thread-1",
    turn_id: turnId,
    item_id: "item-1",
    approval_kind: "command_approval",
    command: "pwd",
    cwd: "/repo",
    reason: null,
    available_decisions: ["accept", "cancel"],
    status: "pending",
    decision: null,
    auto_resolved: false,
    resolution_reason: null,
  };
}

function sessionWithTwoTurns() {
  return {
    ...createNewSessionState(SESSION, {
      workdir: "/repo",
      runtime: "codex",
    }),
    phase: "done" as const,
    turns: [
      {
        id: "local-old",
        turnId: "old",
        userText: "old",
        items: [],
        status: "done" as const,
        revertible: false,
      },
      {
        id: "local-current",
        turnId: "current",
        userText: "current",
        items: [],
        status: "done" as const,
        revertible: false,
      },
    ],
  };
}

describe("applyPendingApproval", () => {
  it("当前 turn 的 pending 请求进入 awaiting_input", () => {
    const next = applyPendingApproval(
      sessionWithTwoTurns(),
      request("current"),
    );

    expect(next.phase).toBe("awaiting_input");
    expect(next.turns[1].items[0]).toMatchObject({
      kind: "approval",
      requestId: "request-current",
    });
  });

  it("旧 turn 的恢复请求不改变当前 phase", () => {
    const next = applyPendingApproval(
      sessionWithTwoTurns(),
      request("old"),
    );

    expect(next.phase).toBe("done");
    expect(next.turns[0].items[0]).toMatchObject({
      kind: "approval",
      requestId: "request-old",
    });
  });
});
