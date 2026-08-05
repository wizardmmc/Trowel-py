import { describe, expect, it } from "vitest";

import type { AgentSession } from "../../agent/transport";
import type { AgentEvent } from "../../agent/transport";
import { replayAgentHistory } from "../../agent/application/store/historyState";
import { createNewSessionState } from "../../agent/application/store/sessionState";

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
    thread_id: null,
    turn_id: null,
    item_id: null,
    payload,
  };
}

describe("replayAgentHistory", () => {
  it("把 Codex 历史命令恢复为可展示的工具条目", () => {
    const codex = createNewSessionState(
      { ...SESSION, runtime: "codex", native_session_id: "thread-1" },
      { workdir: "/repo", runtime: "codex" },
    );
    const replayed = replayAgentHistory(codex, [
      { ...event(1, "user", { text: "inspect" }), runtime: "codex" },
      {
        ...event(2, "tool_call", {
          tool_use_id: "read-1",
          tool_name: "command",
          input: {
            command: "sed -n '1,20p' README.md",
            cwd: "/repo",
            source: "unifiedExecStartup",
            command_actions: [
              {
                type: "read",
                command: "sed -n '1,20p' README.md",
                name: "README.md",
                path: "/repo/README.md",
              },
            ],
          },
        }),
        runtime: "codex",
        item_id: "read-1",
      },
      {
        ...event(3, "tool_result", {
          tool_use_id: "read-1",
          content: "# Trowel",
          exit_code: 0,
          status: "completed",
        }),
        runtime: "codex",
        item_id: "read-1",
      },
      { ...event(4, "finished", {}), runtime: "codex" },
    ]);

    expect(replayed.turns[0].items[0]).toMatchObject({
      kind: "tool",
      toolUseId: "read-1",
      toolName: "command",
      status: "done",
      result: "# Trowel",
      input: {
        command_actions: [
          { type: "read", name: "README.md", path: "/repo/README.md" },
        ],
      },
    });
  });

  it("rebuilds Codex child ownership from parent thread history", () => {
    const codex = createNewSessionState(
      { ...SESSION, runtime: "codex", native_session_id: "parent-thread-1" },
      { workdir: "/repo", runtime: "codex" },
    );
    const replayed = replayAgentHistory(codex, [
      {
        ...event(1, "user", { text: "delegate" }),
        runtime: "codex",
        thread_id: "parent-thread-1",
      },
      {
        ...event(2, "subagent_activity", {
          source: "subagent_activity",
          kind: "started",
          agent_thread_id: "child-thread-1",
          agent_path: "/root/probe",
        }),
        runtime: "codex",
        thread_id: "parent-thread-1",
      },
    ]);

    expect(replayed.codexSubagents["child-thread-1"].parentThreadId).toBe(
      "parent-thread-1",
    );
    expect(replayed.turns[0].items[0]).toMatchObject({
      kind: "subagent",
      subagent: { agentThreadId: "child-thread-1" },
    });
  });

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

  it("历史缺少 tool_result 时把工具收敛为结果事件缺失", () => {
    const session = createNewSessionState(SESSION, { workdir: "/repo" });
    const replayed = replayAgentHistory(session, [
      event(1, "user", { text: "问题" }),
      {
        ...event(2, "tool_call", {
          tool_use_id: "tool-1",
          tool_name: "Read",
          input: { file_path: "/repo/README.md" },
        }),
        item_id: "tool-1",
      },
    ]);

    expect(replayed.turns[0].items[0]).toMatchObject({
      kind: "tool",
      status: "missing",
    });
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

  it("每次历史回放都从零重建压缩次数", () => {
    const session = {
      ...createNewSessionState(SESSION, { workdir: "/repo" }),
      meta: {
        ...createNewSessionState(SESSION, { workdir: "/repo" }).meta,
        compactionCount: 3,
      },
    };
    const history = [
      event(1, "user", { text: "问题" }),
      event(2, "compact_boundary", {}),
    ];

    const first = replayAgentHistory(session, history);
    const second = replayAgentHistory(first, history);

    expect(first.meta.compactionCount).toBe(1);
    expect(second.meta.compactionCount).toBe(1);
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
