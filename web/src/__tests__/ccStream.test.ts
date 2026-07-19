import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { postMessageStream, parseSseFrames } from "../api/ccStream";
import type { AgentEvent } from "../api/agentTypes";

/** Build an AgentEvent v1 envelope JSON string (slice-074 wire shape). */
function env(partial: Partial<AgentEvent> & { type: string; seq: number }): string {
  return JSON.stringify({
    schema: "agent-event-v1",
    session_id: "s1",
    runtime: "claude_code",
    turn_id: null,
    item_id: null,
    payload: {},
    ...partial,
  } satisfies AgentEvent);
}

/**
 * Build a ReadableStream that emits the given chunks as UTF-8 bytes, with
 * microtask gaps so the reader sees them as separate reads.
 */
function makeStream(chunks: string[]): ReadableStream<Uint8Array> {
  const encoder = new TextEncoder();
  return new ReadableStream({
    start(controller) {
      for (const c of chunks) {
        controller.enqueue(encoder.encode(c));
      }
      controller.close();
    },
  });
}

describe("parseSseFrames", () => {
  it("splits a buffer on blank lines into data frames", () => {
    const events = parseSseFrames(
      `data: ${env({ type: "text", seq: 1, payload: { text: "a" } })}\n\n` +
        `data: ${env({ type: "finished", seq: 2, payload: { total_cost_usd: 0, num_turns: 1 } })}\n\n`,
    );
    expect(events).toHaveLength(2);
    expect(events[0].type).toBe("text");
    expect(events[1].type).toBe("finished");
  });

  it("ignores non-data lines (comments / event tags)", () => {
    const events = parseSseFrames(
      `: ping\nevent: x\ndata: ${env({ type: "stalled_warning", seq: 1, payload: { severity: "mild", elapsed_s: 120 } })}\n\n`,
    );
    expect(events).toHaveLength(1);
    expect(events[0].type).toBe("stalled_warning");
  });

  it("skips malformed data lines without throwing", () => {
    const events = parseSseFrames(
      `data: not json\ndata: ${env({ type: "text", seq: 1, payload: { text: "ok" } })}\n\n`,
    );
    expect(events).toHaveLength(1);
    expect((events[0].payload as { text: string }).text).toBe("ok");
  });
});

describe("postMessageStream", () => {
  beforeEach(() => {
    vi.stubGlobal("fetch", vi.fn());
  });
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("parses chunked SSE across read boundaries and forwards events", async () => {
    // Build real JSON via stringify, then split the text frame mid-value so the
    // SSE frame boundary cuts across a read chunk (the carry-buffer case).
    const ss = env({
      type: "session_started",
      seq: 1,
      payload: { model: "g", cwd: "/x", cc_session_id: "s1", tools: [] },
    });
    const textJson = env({ type: "text", seq: 2, payload: { text: "hello" } });
    const fin = env({
      type: "finished",
      seq: 3,
      payload: { total_cost_usd: 0.01, num_turns: 1 },
    });
    // split textJson into "...text\":\"he" | "llo\"}" (mid-value cut)
    const cut = textJson.indexOf("llo");
    const textHead = textJson.slice(0, cut);
    const textTail = textJson.slice(cut);

    const stream = makeStream([
      `data: ${ss}\n\n`,
      `data: ${textHead}`,
      `${textTail}\n\n`,
      `data: ${fin}\n\n`,
    ]);
    vi.mocked(fetch).mockResolvedValue({
      ok: true,
      body: stream,
    } as Response);

    const received: AgentEvent[] = [];
    await postMessageStream("http://x/api", { text: "hi" }, (ev) => received.push(ev));

    expect(received.map((e) => e.type)).toEqual([
      "session_started",
      "text",
      "finished",
    ]);
    expect((received[1].payload as { text: string }).text).toBe("hello");
    // seq passes through unchanged
    expect(received.map((e) => e.seq)).toEqual([1, 2, 3]);
  });

  it("throws on non-ok response", async () => {
    vi.mocked(fetch).mockResolvedValue({ ok: false, status: 500 } as Response);
    await expect(
      postMessageStream("http://x/api", { text: "hi" }, () => {}),
    ).rejects.toThrow();
  });

  it("aborts the underlying fetch when the controller is aborted", async () => {
    const stream = makeStream([
      `data: ${env({ type: "stalled_warning", seq: 1, payload: {} })}\n\n`,
    ]);
    vi.mocked(fetch).mockImplementation((_url, init) => {
      return new Promise((_resolve, reject) => {
        init?.signal?.addEventListener("abort", () => {
          const err = new Error("aborted");
          err.name = "AbortError";
          reject(err);
        });
      }) as never;
    });
    void stream; // stream unused in this branch; satisfy lint

    const ctrl = new AbortController();
    const promise = postMessageStream(
      "http://x/api",
      { text: "hi" },
      () => {},
      { signal: ctrl.signal },
    );
    ctrl.abort();
    // aborted fetch must not reject the outer promise (silent on user abort)
    await expect(promise).resolves.toBeUndefined();
  });
});
