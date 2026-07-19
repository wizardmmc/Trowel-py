/**
 * slice-072: /api/agent client — mirrors the host-neutral Session Hub routes.
 * Each test pins one endpoint's URL + method + body shape.
 */
import { describe, it, expect, vi, beforeEach } from "vitest";

import {
  activateAgentSession,
  agentMessagesUrl,
  createAgentSession,
  deleteAgentSession,
  getAgentSession,
  interruptAgentSession,
  listActiveAgentSessions,
  listAgentHistory,
  listAgentRuntimes,
} from "../api/agent";

function mockEnvelope(data: unknown, ok = true): Response {
  return new Response(
    JSON.stringify({ success: ok, data, error: ok ? null : "boom" }),
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

  it("listAgentHistory encodes the workdir", async () => {
    const spy = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(
        mockEnvelope([{ runtime: "codex", native_session_id: "t1" }]),
      );
    await listAgentHistory("/tmp/a b");
    expect((spy.mock.calls[0][0] as string)).toBe(
      "/api/agent/sessions?workdir=" + encodeURIComponent("/tmp/a b"),
    );
  });

  it("agentMessagesUrl builds the SSE endpoint", () => {
    expect(agentMessagesUrl("s1")).toBe("/api/agent/sessions/s1/messages");
  });

  it("throws when the envelope reports an error", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(mockEnvelope(null, false));
    await expect(getAgentSession("s1")).rejects.toThrow("boom");
  });
});
