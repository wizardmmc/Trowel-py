import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import {
  createSession,
  listSessions,
  listModels,
  listSlashItems,
  listDir,
  getHistory,
  interruptSession,
  deleteSession,
  messagesUrl,
} from "../api/cc";

function mockFetchEnvelope(data: unknown, success = true) {
  return vi.fn().mockResolvedValue({
    ok: true,
    json: async () => ({ success, data, error: success ? null : "boom" }),
  });
}

describe("cc REST client", () => {
  beforeEach(() => {
    vi.stubGlobal("fetch", vi.fn());
  });
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("createSession POSTs the params and returns the session", async () => {
    vi.mocked(fetch).mockResolvedValue({
      ok: true,
      json: async () => ({
        success: true,
        data: { session_id: "s1", cc_session_id: null, model: "glm-5.2" },
        error: null,
      }),
    } as Response);

    const session = await createSession({ workdir: "/wd", effort: "low" });

    expect(session.session_id).toBe("s1");
    const call = vi.mocked(fetch).mock.calls[0];
    const body = JSON.parse((call?.[1]?.body as string) ?? "{}");
    expect(body).toEqual({ workdir: "/wd", effort: "low" });
    expect(call?.[1]?.method).toBe("POST");
  });

  it("listSessions returns capped sessions + total from meta", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: true,
        json: async () => ({
          success: true,
          data: [{ cc_session_id: "a", title: "t", updated_at: 1 }],
          error: null,
          meta: { total: 42, limit: 10 },
        }),
      }),
    );
    const result = await listSessions("/some dir/path");
    expect(result.sessions).toHaveLength(1);
    expect(result.total).toBe(42);
    const url = vi.mocked(fetch).mock.calls[0]?.[0] as string;
    expect(url).toContain("workdir=%2Fsome%20dir%2Fpath");
  });

  it("listSessions falls back to data length when meta is absent", async () => {
    vi.stubGlobal(
      "fetch",
      mockFetchEnvelope([{ cc_session_id: "a", title: "t", updated_at: 1 }]),
    );
    const result = await listSessions("/wd");
    expect(result.sessions).toHaveLength(1);
    expect(result.total).toBe(1);
  });

  it("getHistory returns the event list", async () => {
    vi.stubGlobal(
      "fetch",
      mockFetchEnvelope([{ type: "user", text: "hi" }, { type: "text", text: "yo" }]),
    );
    const events = await getHistory("s1");
    expect(events.map((e) => e.type)).toEqual(["user", "text"]);
  });

  it("interruptSession and deleteSession hit the right methods", async () => {
    const mock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ success: true, data: { ok: true }, error: null }),
    });
    vi.stubGlobal("fetch", mock);

    await interruptSession("s1");
    expect(mock.mock.calls[0]?.[1]?.method).toBe("POST");

    await deleteSession("s1");
    expect(mock.mock.calls[1]?.[1]?.method).toBe("DELETE");
  });

  it("messagesUrl builds the stream endpoint", () => {
    expect(messagesUrl("s1")).toBe(
      "/api/cc/sessions/s1/messages",
    );
  });

  it("throws on envelope-level error", async () => {
    vi.stubGlobal("fetch", mockFetchEnvelope(null, false));
    await expect(createSession({ workdir: "/wd" })).rejects.toThrow("boom");
  });

  it("listModels GETs /models and returns the alias rows", async () => {
    vi.stubGlobal(
      "fetch",
      mockFetchEnvelope([
        { value: "opus", label: "Opus", real_model: "glm-5.2[1M]",
          description: "最强推理" },
      ]),
    );
    const out = await listModels();
    expect(out[0].value).toBe("opus");
    expect(out[0].real_model).toBe("glm-5.2[1M]");
    const url = vi.mocked(fetch).mock.calls[0]?.[0] as string;
    expect(url).toBe("/api/cc/models");
  });

  it("listSlashItems sends workdir query and returns items", async () => {
    vi.stubGlobal(
      "fetch",
      mockFetchEnvelope([
        { name: "monthly-etf", description: "月度ETF", source: "user",
          type: "skill" },
      ]),
    );
    const out = await listSlashItems("/wd");
    expect(out[0].name).toBe("monthly-etf");
    expect(out[0].type).toBe("skill");
    const url = vi.mocked(fetch).mock.calls[0]?.[0] as string;
    expect(url).toContain("/slash-items");
    expect(url).toContain("workdir=%2Fwd");
  });

  it("listDir sends path query and returns subdirs", async () => {
    vi.stubGlobal(
      "fetch",
      mockFetchEnvelope([{ name: "sub", path: "/x/sub" }]),
    );
    const out = await listDir("/x");
    expect(out[0].name).toBe("sub");
    expect(out[0].path).toBe("/x/sub");
    const url = vi.mocked(fetch).mock.calls[0]?.[0] as string;
    expect(url).toContain("/list-dir");
    expect(url).toContain("path=%2Fx");
  });
});
