import { describe, it, expect, vi, beforeEach } from "vitest";

import {
  activateAgentSession,
  agentMessagesUrl,
  agentEventsUrl,
  answerAgentRequest,
  createAgentSession,
  deleteAgentSession,
  getAgentSessionDefaults,
  getAgentSession,
  getCodexSubagentHistory,
  interruptAgentSession,
  listActiveAgentSessions,
  listAgentHistory,
  listAgentModels,
  listAgentRequests,
  listAgentRuntimes,
  listCodexCommands,
  compactCodexSession,
  startCodexReview,
  getCodexGoal,
  setCodexGoal,
  clearCodexGoal,
  startCodexTurn,
  startAgentTurn,
  AgentTransportError,
  updateAgentSessionSettings,
} from "../agent/transport";

function mockEnvelope(data: unknown, ok = true, meta?: unknown): Response {
  return new Response(
    JSON.stringify({ success: ok, data, meta, error: ok ? null : "boom" }),
  );
}

describe("api/agent", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it("createAgentSession POSTs the runtime-tagged body", async () => {
    const spy = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(mockEnvelope({ session_id: "s1", runtime: "codex" }));

    const session = await createAgentSession(
      {
        runtime: "codex",
        workdir: "/tmp/proj",
        model: "gpt-5.6-sol",
      },
      "stable-create-id",
    );
    expect(session.session_id).toBe("s1");

    const [url, init] = spy.mock.calls[0];
    expect(url).toBe("/api/agent/sessions");
    expect((init as RequestInit).method).toBe("POST");
    expect(new Headers((init as RequestInit).headers).get("X-Trowel-Request-Id"))
      .toBe("stable-create-id");
    expect(JSON.parse((init as RequestInit).body as string)).toEqual({
      runtime: "codex",
      workdir: "/tmp/proj",
      model: "gpt-5.6-sol",
    });
  });

  it("listActiveAgentSessions unwraps sessions + active_id", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      mockEnvelope({
        sessions: [{ session_id: "s1", runtime: "claude_code" }],
        active_id: "s1",
      }),
    );
    const result = await listActiveAgentSessions();
    expect(result.sessions).toHaveLength(1);
    expect(result.activeId).toBe("s1");
    expect(
      (vi.mocked(globalThis.fetch).mock.calls[0][0] as string),
    ).toBe("/api/agent/sessions/active");
  });

  it("getAgentSessionDefaults returns the last effective launch config", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      mockEnvelope({
        runtime: "codex",
        model: "gpt-5.6-sol",
        effort: "high",
        permission_mode: "",
        permission_preset: "workspace-write",
        memory_enabled: true,
        profile_enabled: false,
      }),
    );

    const defaults = await getAgentSessionDefaults();

    expect(defaults?.runtime).toBe("codex");
    expect(defaults?.effort).toBe("high");
    expect(vi.mocked(globalThis.fetch).mock.calls[0][0]).toBe(
      "/api/agent/session-defaults",
    );
  });

  it("activateAgentSession POSTs to /activate", async () => {
    const spy = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(mockEnvelope({ active_id: "s2" }));
    const { activeId } = await activateAgentSession("s2");
    expect(activeId).toBe("s2");
    const [url, init] = spy.mock.calls[0];
    expect(url).toBe("/api/agent/sessions/s2/activate");
    expect((init as RequestInit).method).toBe("POST");
  });

  it("getAgentSession GETs one binding", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      mockEnvelope({ session_id: "s1", runtime: "codex" }),
    );
    const session = await getAgentSession("s1");
    expect(session.session_id).toBe("s1");
    expect(
      (vi.mocked(globalThis.fetch).mock.calls[0][0] as string),
    ).toBe("/api/agent/sessions/s1");
  });

  it("deleteAgentSession DELETEs", async () => {
    const spy = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(
        mockEnvelope({
          closed: true,
          status: "closed",
          remaining_resource_count: 0,
          remaining_resource_kinds: [],
          error: null,
        }),
      );
    const result = await deleteAgentSession("s1");
    expect(result).toEqual({
      closed: true,
      status: "closed",
      remaining_resource_count: 0,
      remaining_resource_kinds: [],
      error: null,
    });
    const [url, init] = spy.mock.calls[0];
    expect(url).toBe("/api/agent/sessions/s1");
    expect((init as RequestInit).method).toBe("DELETE");
  });

  it("interruptAgentSession POSTs to /interrupt", async () => {
    const spy = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(mockEnvelope({ interrupted: true }));
    await interruptAgentSession("s1");
    const [url, init] = spy.mock.calls[0];
    expect(url).toBe("/api/agent/sessions/s1/interrupt");
    expect((init as RequestInit).method).toBe("POST");
  });

  it("turn start timeout reports acceptance as unknown", async () => {
    vi.useFakeTimers();
    vi.spyOn(globalThis, "fetch").mockImplementation((_url, init) =>
      new Promise((_resolve, reject) => {
        init?.signal?.addEventListener("abort", () => reject(init.signal?.reason));
      }),
    );
    try {
      const request = startAgentTurn("s1", "hello");
      void request.catch(() => {});
      await vi.advanceTimersByTimeAsync(30_000);
      const error = await request.catch((reason: unknown) => reason);
      expect(error).toBeInstanceOf(AgentTransportError);
      expect((error as AgentTransportError).problem).toMatchObject({
        code: "turn_acceptance_unknown",
        operation: "turn_start",
        budgetMs: 30_000,
      });
    } finally {
      vi.useRealTimers();
    }
  });

  it("turn start transport failure also reports acceptance as unknown", async () => {
    vi.spyOn(globalThis, "fetch").mockRejectedValueOnce(
      new TypeError("response connection closed"),
    );

    await expect(startAgentTurn("s1", "hello")).rejects.toMatchObject({
      problem: {
        code: "turn_acceptance_unknown",
        operation: "turn_start",
      },
    });
  });

  it("turn start deadline also covers a stalled response body", async () => {
    vi.useFakeTimers();
    vi.spyOn(globalThis, "fetch").mockImplementation(async (_url, init) => ({
      ok: true,
      json: () =>
        new Promise((_resolve, reject) => {
          init?.signal?.addEventListener("abort", () =>
            reject(init.signal?.reason),
          );
        }),
    }) as Response);
    try {
      const request = startAgentTurn("s1", "hello");
      void request.catch(() => {});
      await vi.advanceTimersByTimeAsync(30_000);

      await expect(request).rejects.toMatchObject({
        problem: { code: "turn_acceptance_unknown" },
      });
    } finally {
      vi.useRealTimers();
    }
  });

  it("listAgentRuntimes returns the runtime catalog", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      mockEnvelope([
        {
          runtime: "claude_code",
          label: "Claude Code",
          native: "claude -p",
          capabilities: ["tools"],
          connected: true,
        },
      ]),
    );
    const runtimes = await listAgentRuntimes();
    expect(runtimes[0].runtime).toBe("claude_code");
    expect(runtimes[0].capabilities).toEqual(["tools"]);
  });

  it("listAgentModels returns unknown native rows unchanged", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      mockEnvelope({
        models: [
          {
            id: "future-model",
            model: "future-native",
            display_name: "Future",
            description: "future",
            is_default: true,
            default_effort: "quantum",
            supported_efforts: [
              { value: "quantum", description: "future effort" },
            ],
          },
        ],
      }),
    );
    const models = await listAgentModels();
    expect(models[0].supported_efforts[0].value).toBe("quantum");
    expect(vi.mocked(globalThis.fetch).mock.calls[0][0]).toBe(
      "/api/agent/models",
    );
  });

  it("listAgentModels waits 30 seconds before the renderer fallback", async () => {
    vi.useFakeTimers();
    vi.spyOn(globalThis, "fetch").mockImplementation((_url, init) =>
      new Promise((_resolve, reject) => {
        init?.signal?.addEventListener("abort", () => reject(init.signal?.reason));
      }),
    );

    try {
      const request = listAgentModels();
      let rejected = false;
      void request.catch(() => {
        rejected = true;
      });
      await vi.advanceTimersByTimeAsync(29_999);
      expect(
        (vi.mocked(globalThis.fetch).mock.calls[0][1] as RequestInit).signal
          ?.aborted,
      ).toBe(false);
      expect(rejected).toBe(false);
      await vi.advanceTimersByTimeAsync(1);
      await expect(request).rejects.toThrow(
        "Codex model catalog request timed out",
      );
      expect(
        (vi.mocked(globalThis.fetch).mock.calls[0][1] as RequestInit).signal
          ?.aborted,
      ).toBe(true);
    } finally {
      vi.useRealTimers();
    }
  });

  it("listAgentModels surfaces the backend timeout message", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(
        JSON.stringify({
          success: false,
          data: null,
          error: {
            code: "request_timeout",
            message: "Codex model catalog request timed out",
          },
        }),
        { status: 504 },
      ),
    );

    await expect(listAgentModels()).rejects.toThrow(
      "Codex model catalog request timed out",
    );
  });

  it("updateAgentSessionSettings PATCHes model and effort together", async () => {
    const spy = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      mockEnvelope({ model: "gpt-5.6-luna", effort: "medium", adjusted: true }),
    );
    const selected = await updateAgentSessionSettings("s1", {
      model: "gpt-5.6-luna",
      effort: "ultra",
    });
    expect(selected.adjusted).toBe(true);
    const [url, init] = spy.mock.calls[0];
    expect(url).toBe("/api/agent/sessions/s1");
    expect((init as RequestInit).method).toBe("PATCH");
    expect(JSON.parse((init as RequestInit).body as string)).toEqual({
      model: "gpt-5.6-luna",
      effort: "ultra",
    });
  });

  it("listAgentHistory encodes workdir, limit and opaque cursor", async () => {
    const spy = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(
        mockEnvelope(
          [{ runtime: "codex", native_session_id: "t1" }],
          true,
          { limit: 20, next_cursor: "opaque-next" },
        ),
      );
    const page = await listAgentHistory("/tmp/a b", {
      limit: 20,
      cursor: "opaque-current",
    });
    expect((spy.mock.calls[0][0] as string)).toBe(
      "/api/agent/sessions?workdir=" +
        encodeURIComponent("/tmp/a b") +
        "&limit=20&cursor=opaque-current",
    );
    expect(page.rows).toHaveLength(1);
    expect(page.nextCursor).toBe("opaque-next");
  });

  it("agentMessagesUrl builds the SSE endpoint", () => {
    expect(agentMessagesUrl("s1")).toBe("/api/agent/sessions/s1/messages");
  });

  it("loads a Codex child history with an encoded native thread id", async () => {
    const spy = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(mockEnvelope([]));

    await getCodexSubagentHistory("s1", "child/thread 1");

    expect(spy.mock.calls[0][0]).toBe(
      "/api/agent/sessions/s1/subagents/child%2Fthread%201/history",
    );
  });

  it("builds the Codex event endpoint and normalizes Goal CRUD", async () => {
    const goal = {
      threadId: "t1",
      objective: "Ship the rail",
      status: "active",
      tokenBudget: 12000,
      tokensUsed: 7448,
      timeUsedSeconds: 9,
      createdAt: 10,
      updatedAt: 11,
    };
    const spy = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(mockEnvelope({ goal }))
      .mockResolvedValueOnce(mockEnvelope({ goal }))
      .mockResolvedValueOnce(mockEnvelope({ cleared: true }))
      .mockResolvedValueOnce(mockEnvelope({ turn_id: "turn-1" }));

    expect(agentEventsUrl()).toBe("/api/agent/events");
    expect((await getCodexGoal("s1"))?.tokensUsed).toBe(7448);
    await setCodexGoal("s1", { objective: "Ship the rail", token_budget: 12000 });
    expect(await clearCodexGoal("s1")).toEqual({ cleared: true });
    expect(await startCodexTurn("s1", "continue")).toEqual({ turnId: "turn-1" });
    expect(spy.mock.calls.map(([url]) => url)).toEqual([
      "/api/agent/sessions/s1/goal",
      "/api/agent/sessions/s1/goal",
      "/api/agent/sessions/s1/goal",
      "/api/agent/sessions/s1/turns",
    ]);
  });

  it("loads and executes Codex native commands without using /turns", async () => {
    const command = {
      name: "review",
      description: "Review changes",
      source: "codex",
      action: "review",
      available_while_running: false,
    } as const;
    const spy = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(mockEnvelope({ commands: [command] }))
      .mockResolvedValueOnce(mockEnvelope({ started: true }))
      .mockResolvedValueOnce(
        mockEnvelope({ review_thread_id: "thread-1", turn_id: "turn-1" }),
      );

    await expect(listCodexCommands("s1")).resolves.toEqual([command]);
    await expect(compactCodexSession("s1")).resolves.toEqual({ started: true });
    await expect(
      startCodexReview("s1", { type: "commit", sha: "abc123", title: "Fix" }),
    ).resolves.toEqual({ reviewThreadId: "thread-1", turnId: "turn-1" });

    expect(spy.mock.calls.map(([url]) => url)).toEqual([
      "/api/agent/sessions/s1/commands",
      "/api/agent/sessions/s1/commands/compact",
      "/api/agent/sessions/s1/commands/review",
    ]);
    expect(JSON.parse((spy.mock.calls[2][1] as RequestInit).body as string)).toEqual({
      target: { type: "commit", sha: "abc123", title: "Fix" },
    });
  });

  it("answers a pending Codex request through the host-neutral API", async () => {
    const request = {
      request_id: "7-0",
      session_id: "s1",
      thread_id: "t1",
      turn_id: "turn-1",
      item_id: "exec-1",
      approval_kind: "command_approval",
      command: "pwd",
      cwd: "/tmp",
      reason: "Allow it?",
      available_decisions: ["accept", "cancel"],
      status: "answered",
      decision: "cancel",
      auto_resolved: false,
      resolution_reason: null,
    } as const;
    const spy = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(mockEnvelope({ answered: true, request }));

    const result = await answerAgentRequest("s1", "7-0", "cancel");

    const [url, init] = spy.mock.calls[0];
    expect(url).toBe("/api/agent/sessions/s1/requests/7-0/answer");
    expect((init as RequestInit).method).toBe("POST");
    expect(JSON.parse((init as RequestInit).body as string)).toEqual({
      decision: "cancel",
    });
    expect(result.request.status).toBe("answered");
  });

  it("lists retained requests for disconnect recovery", async () => {
    const spy = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(mockEnvelope({ requests: [] }));

    await expect(listAgentRequests("s1")).resolves.toEqual([]);
    expect(spy.mock.calls[0][0]).toBe("/api/agent/sessions/s1/requests");
  });

  it("throws when the envelope reports an error", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(mockEnvelope(null, false));
    await expect(getAgentSession("s1")).rejects.toThrow("boom");
  });

  it("preserves the backend reason for non-2xx responses", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ detail: "session already has an active turn" }), {
        status: 409,
        headers: { "Content-Type": "application/json" },
      }),
    );

    await expect(compactCodexSession("s1")).rejects.toThrow(
      "session already has an active turn",
    );
  });
});
