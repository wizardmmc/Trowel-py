import { describe, it, expect, vi, beforeEach } from "vitest";

import {
  activateAgentSession,
  agentMessagesUrl,
  answerAgentRequest,
  createAgentSession,
  deleteAgentSession,
  getAgentSessionDefaults,
  getAgentSession,
  interruptAgentSession,
  listActiveAgentSessions,
  listAgentHistory,
  listAgentModels,
  listAgentRequests,
  listAgentRuntimes,
  updateAgentSessionSettings,
} from "../api/agent";

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

    const session = await createAgentSession({
      runtime: "codex",
      workdir: "/tmp/proj",
      model: "gpt-5.6-sol",
    });
    expect(session.session_id).toBe("s1");

    const [url, init] = spy.mock.calls[0];
    expect(url).toBe("/api/agent/sessions");
    expect((init as RequestInit).method).toBe("POST");
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
      .mockResolvedValue(mockEnvelope({ closed: true }));
    const { closed } = await deleteAgentSession("s1");
    expect(closed).toBe(true);
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
});
