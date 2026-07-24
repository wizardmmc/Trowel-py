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
});
